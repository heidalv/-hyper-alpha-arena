"""中长线持仓管理模式（Phase 5）— 模式 B 六维仓位发展分析。

开仓后，分析大脑从「入场思维」切换到「持仓发展思维」：不再反复问
「要不要开新仓」，而是围绕已持仓交易对做仓位发展分析。

六维分析（设计文档 MIDLONG_V2_ARCHITECTURE_DESIGN §4.6 / 审计报告 §7.3）：
  ① 方向延续性   trend_agent.review_position          → hold / reduce / close / tighten
  ② 滚仓(金字塔)  trend_agent.evaluate_pyramid + 5层门控 → add / wait / skip
  ③ TP/SL 调整   review_position.trend_adjustment      → update_position_tp_sl
  ④ 补仓(DCA)    默认禁止（浮亏加仓=自杀原则）          → skip
  ⑤ 分批止盈      long_tier_staged_tp.check            → reduce / trailing_update / trailing_hit
  ⑥ 反转离场      evaluate_midlong_exit + no_progress   → close

执行链路（§7.5，单一优先级，每 tick 最多执行一个实质动作）：
  close  （⑥反转 > ⑤trailing_hit > ①方向破坏） → paper_engine.close_position
  reduce （⑤分档止盈）                          → paper_engine.close_position(部分)
  add    （② 浮盈+LLM add，或浮盈>5% 规则直通） → position_manager.evaluate_pyramid → place_order(add_type="pyramid")
  tighten（①收紧追踪止损 / ③ TP上移）          → paper_engine.update_position_tp_sl
  reduce （①方向减弱，仅浮亏/平盘执行）         → paper_engine.close_position(部分)
  hold                                          → 更新趋势复查时间戳，继续持有

频率（§7.6）：
  - 规则维度（⑤⑥）每 tick 执行（零成本，实时响应反转/分档止盈）。
  - LLM 维度（①②③）受 MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC（默认 900s）节流，
    复用 exit_state_json.last_trend_review_ts（与 run_trend_review 同 key：
    模式 B 接管后，90min 的 run_trend_review 兜底自动休眠）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# 模块级内存节流（多 session 多账号用 f"{account_id}:{symbol}" 隔离）
# ──────────────────────────────────────────────────────────────────────
_last_llm_run_ts: Dict[str, float] = {}  # LLM 维度（①②③）最近一次执行时间戳
_last_global_run_ts: Dict[str, float] = {}  # 模式 B 整体最近一次执行时间戳（INTERVAL_SEC 用）


def _cfg_bool(key: str, default: bool = True) -> bool:
    try:
        from backend.config import settings
        return bool(getattr(settings, key, default))
    except Exception:
        raw = os.getenv(key, "true" if default else "false").strip().lower()
        return raw in ("1", "true", "yes", "on")


def _cfg_int(key: str, default: int) -> int:
    try:
        from backend.config import settings
        return int(getattr(settings, key, default) or 0)
    except Exception:
        try:
            return int(os.getenv(key, str(default)))
        except Exception:
            return default


def _cfg_float(key: str, default: float) -> float:
    try:
        from backend.config import settings
        return float(getattr(settings, key, default) or default)
    except Exception:
        try:
            return float(os.getenv(key, str(default)))
        except Exception:
            return default


# ──────────────────────────────────────────────────────────────────────
# 模式切换判定：该交易对是否有未平仓中长线仓位
# ──────────────────────────────────────────────────────────────────────
def has_open_midlong_position(db, account_id, symbol: str) -> bool:
    """模式切换判定（§7.2）：交易对 + 未平仓中长线仓位 → 模式 B。

    判定依据：`PaperPosition.status=open`，且 tier∈(mid,long)
    或 trade_nature∈(trend_follow,swing,position)。
    """
    if not account_id or db is None:
        return False
    sym_u = str(symbol or "").upper()
    try:
        from backend.database.models import PaperPosition
        pos = db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == sym_u,
            PaperPosition.status == "open",
        ).first()
        if pos is None:
            return False
        tier = str(getattr(pos, "timeframe_tier", "") or "").lower()
        nature = str(getattr(pos, "trade_nature", "") or "").lower()
        return tier in ("mid", "long") or nature in ("trend_follow", "swing", "position")
    except Exception as e:
        logger.warning("[MidLong] 模式切换持仓判定异常 %s: %s", sym_u, e)
        return False


def _open_midlong_positions(db, account_id) -> List[Dict[str, Any]]:
    """从 paper_engine 拿该账号全部未平仓中长线仓位（dict 列表）。"""
    if not account_id:
        return []
    try:
        from backend.services.paper_trading_engine import paper_engine
        positions = paper_engine.get_positions(db, account_id) or []
        out = []
        for p in positions:
            if str(p.get("status", "open")).lower() != "open":
                continue
            tier = str(p.get("timeframe_tier") or "").lower()
            nature = str(p.get("trade_nature") or "").lower()
            if tier in ("mid", "long") or nature in ("trend_follow", "swing", "position"):
                out.append(p)
        return out
    except Exception as e:
        logger.warning("[MidLong] stage=manage 拉取持仓失败: %s", e)
        return []


# ──────────────────────────────────────────────────────────────────────
# 持仓上下文辅助
# ──────────────────────────────────────────────────────────────────────
def _pos_direction(side: Any) -> str:
    s = str(side or "").lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return ""


def _tier_of(position: Dict[str, Any]) -> str:
    tier = str(position.get("timeframe_tier") or "").lower()
    if tier in ("mid", "long"):
        return tier
    nature = str(position.get("trade_nature") or "").lower()
    if nature in ("trend_follow", "position"):
        return "long"
    if nature == "swing":
        return "mid"
    return "mid"


def _held_hours(position: Dict[str, Any], db=None) -> float:
    """持仓时长（小时）。优先 ORM 的 opened_at；dict 兜底。"""
    pid = position.get("id")
    if pid and db is not None:
        try:
            from backend.database.models import PaperPosition
            p = db.query(PaperPosition).filter(PaperPosition.id == int(pid)).first()
            if p is not None:
                opened = getattr(p, "opened_at", None) or getattr(p, "created_at", None)
                if opened is not None:
                    return max(0.0, (time.time() - opened.timestamp()) / 3600.0)
        except Exception:
            pass
    for key in ("opened_at", "created_at", "entry_time", "open_time"):
        val = position.get(key)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                return max(0.0, (time.time() - float(val)) / 3600.0)
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
        except Exception:
            continue
    return float(position.get("hold_hours", 0) or 0)


def _pnl_pct_of(position: Dict[str, Any]) -> float:
    """浮盈百分比（小数，如 0.042=+4.2%）。

    口径与 trend_pyramid_gate / position_manager.evaluate_pyramid 完全一致：
    upnl / margin（margin 即保证金，等价于 pnl_pct 字段（含杠杆百分数）/100）。
    不采用 abs(x)>1 启发式，避免小浮盈(如 +0.5%)被误判为 50%。
    """
    margin = float(position.get("margin", 0) or 0)
    upnl = float(position.get("unrealized_pnl", 0) or 0)
    if margin > 0:
        return upnl / margin
    # 兜底：读 pnl_pct 字段（_position_to_dict 里是含杠杆百分数 = upnl/margin*100）
    v = position.get("pnl_pct")
    if v is not None:
        try:
            return float(v) / 100.0
        except Exception:
            pass
    return 0.0


def _build_gate_market_summary(market_summary: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """为 trend_pyramid_gate 构造最小门控上下文（orchestrator + indicators）。"""
    sym_u = str(symbol or "").upper()
    gate_ms: Dict[str, Any] = {}
    _sym_mkt = market_summary.get(sym_u) or {}
    if isinstance(_sym_mkt, dict):
        _orch = _sym_mkt.get("orchestrator") or {}
        if isinstance(_orch, dict):
            gate_ms["orchestrator"] = {
                "final_action": _orch.get("final_action", "wait"),
                "final_side": _orch.get("final_side", ""),
                "long_view_bias": _orch.get("long_view_bias", "neutral"),
                "mid_view_bias": _orch.get("mid_view_bias", "neutral"),
                "short_view_bias": _orch.get("short_view_bias", "neutral"),
            }
        _ind_1d = _sym_mkt.get("indicators_1d") or {}
        _ind_4h = _sym_mkt.get("indicators_4h") or {}
        if isinstance(_ind_1d, dict) or isinstance(_ind_4h, dict):
            _ind: Dict[str, Any] = dict(_ind_1d or {})
            if isinstance(_ind_4h, dict) and _ind_4h.get("adx") is not None:
                _ind["adx_4h"] = _ind_4h["adx"]
            gate_ms["indicators"] = {sym_u: _ind}
    return gate_ms


# ──────────────────────────────────────────────────────────────────────
# 维度 ⑥：反转 / 无进展离场（规则，每 tick，零成本）
# ──────────────────────────────────────────────────────────────────────
def _dim_reversal(position: Dict[str, Any], market_summary: Dict[str, Any]) -> Dict[str, Any]:
    """bias 强反向 / 无进展 → 主动离场。返回 {"action": "close"/"hold", "reason", "channel"}。"""
    # 口径与 full_auto_trading_service._run_midlong_active_exit 一致：
    # evaluate_midlong_exit 期望该 symbol 的 market_data dict（顶层含 orchestrator 键）
    _sym = str(position.get("symbol") or "").upper()
    _md = market_summary.get(_sym) if isinstance(market_summary, dict) else None
    try:
        from backend.services.midlong_exit_guard import evaluate_midlong_exit
        dec = evaluate_midlong_exit(position, _md)
        if dec.action == "close":
            return {"action": "close", "channel": "bias_reversal", "reason": str(dec.reason or "")}
    except Exception as e:
        logger.debug("[MidLong] stage=manage 反转检测异常: %s", e)
    try:
        from backend.services.mlto.midlong_portfolio_risk import evaluate_no_progress_exit
        np = evaluate_no_progress_exit(position)
        if np.action == "close":
            return {"action": "close", "channel": "no_progress", "reason": str(np.reason or "")}
    except Exception as e:
        logger.debug("[MidLong] stage=manage 无进展检测异常: %s", e)
    return {"action": "hold", "channel": "", "reason": ""}


# ──────────────────────────────────────────────────────────────────────
# 维度 ⑤：分批止盈推进（规则引擎，每 tick，仅 long tier）
# ──────────────────────────────────────────────────────────────────────
def _dim_staged_tp(
    db, *, host, session, account_id, position: Dict[str, Any],
    market_summary: Dict[str, Any],
) -> Dict[str, Any]:
    """浮盈分档减仓 + ATR 追踪。返回 {"action": "hold"/"reduce"/"close", "channel", "reason", "ratio", "new_sl"}。"""
    tier = _tier_of(position)
    if tier != "long":
        return {"action": "hold", "channel": "", "reason": "tier!=long 不分批止盈"}
    try:
        from backend.config.settings import RISK_USE_LONG_TIER_STAGED_TP
        if not RISK_USE_LONG_TIER_STAGED_TP:
            return {"action": "hold", "channel": "", "reason": "flag_off"}
        from backend.services.long_tier_staged_tp import check as _staged_tp_check
        from backend.services.long_tier_staged_tp import StagedTpState

        pid = position.get("id")
        if not pid:
            return {"action": "hold", "channel": "", "reason": "no_pid"}
        entry = float(position.get("entry_price", 0) or 0)
        mark = float(position.get("mark_price", 0) or entry)
        side = _pos_direction(position.get("side"))
        if entry <= 0 or mark <= 0 or not side:
            return {"action": "hold", "channel": "", "reason": "bad_price"}
        sym = str(position.get("symbol", "") or "").upper()
        _sym_mkt = market_summary.get(sym) or {}
        atr_pct = 0.02
        if isinstance(_sym_mkt, dict):
            atr_pct = float(_sym_mkt.get("volatility_value", 0.02) or 0.02)

        state_key = f"pos_{pid}"
        state = host.long_tier_staged_tp_state.get(state_key)
        if state is None:
            state = StagedTpState()
            host.long_tier_staged_tp_state[state_key] = state

        decision = _staged_tp_check(
            entry_price=entry, current_price=mark,
            side="long" if side == "long" else "short",
            atr_pct=atr_pct, state=state,
        )
        act = (decision.action or "hold").lower()
        if act == "reduce":
            ratio = float(getattr(decision, "reduce_ratio", 0.3) or 0.3)
            return {
                "action": "reduce", "channel": f"tp_staged_{(decision.stage_idx or 0) + 1}",
                "reason": str(decision.reason or ""), "ratio": ratio,
            }
        if act == "trailing_hit":
            return {
                "action": "close", "channel": "staged_trailing_hit",
                "reason": str(decision.reason or ""), "ratio": 1.0,
            }
        if act == "trailing_update":
            return {
                "action": "hold", "channel": "staged_trailing_update",
                "reason": str(decision.reason or ""),
                "new_sl": getattr(decision, "suggested_sl_price", None),
            }
        return {"action": "hold", "channel": "", "reason": str(decision.reason or "")}
    except Exception as e:
        logger.debug("[MidLong] stage=manage 分批止盈检查异常: %s", e)
        return {"action": "hold", "channel": "", "reason": f"err:{e}"}


# ──────────────────────────────────────────────────────────────────────
# 维度 ① + ③：方向延续性复查 + TP/SL 调整（LLM，节流）
# ──────────────────────────────────────────────────────────────────────
def _dim_direction(
    db, *, account_id, symbol: str, position: Dict[str, Any],
    market_summary: Dict[str, Any], analyst_reports: Dict[str, Any],
) -> Dict[str, Any]:
    """trend_agent.review_position 六维中的①③。返回 review dict。"""
    from backend.services.trend_agent import trend_agent
    entry = float(position.get("entry_price", 0) or 0)
    mark = float(position.get("mark_price", 0) or entry)
    pnl_pct = _pnl_pct_of(position)
    hold_hours = _held_hours(position, db)
    lev = int(position.get("leverage", 1) or 1)
    _pos_ctx = {
        "entry_price": entry, "mark_price": mark,
        "pnl_pct": pnl_pct * 100.0,  # review 用百分数（含杠杆口径，与 run_trend_review 一致）
        "hold_hours": hold_hours, "leverage": lev,
    }
    return trend_agent.review_position(
        symbol=symbol, side=_pos_direction(position.get("side")) or "long",
        position=_pos_ctx,
        reports=analyst_reports or {},
        market_envs=market_summary or {},
        account_id=account_id, db=db,
    )


# ──────────────────────────────────────────────────────────────────────
# 维度 ②：滚仓（LLM + 5 层门控 + 数量计算）
# ──────────────────────────────────────────────────────────────────────
def _dim_pyramid(
    db, *, account_id, symbol: str, position: Dict[str, Any],
    market_summary: Dict[str, Any], analyst_reports: Dict[str, Any],
) -> Dict[str, Any]:
    """判断是否滚仓。返回 {"action": "add"/"wait"/"skip", "ratio", "reasoning"}。"""
    from backend.services.trend_agent import trend_agent
    entry = float(position.get("entry_price", 0) or 0)
    mark = float(position.get("mark_price", 0) or entry)
    _pos_ctx = {
        "entry_price": entry, "mark_price": mark,
        "pnl_pct": _pnl_pct_of(position) * 100.0,
    }
    return trend_agent.evaluate_pyramid(
        symbol=symbol, side=_pos_direction(position.get("side")) or "long",
        position=_pos_ctx,
        reports=analyst_reports or {},
        market_envs=market_summary or {},
        account_id=account_id,
    )


# ──────────────────────────────────────────────────────────────────────
# 执行出口（复用 paper_engine，不新建平仓/加仓路径）
# ──────────────────────────────────────────────────────────────────────
def _exec_close(db, *, account_id, position, reason: str, host, session) -> Optional[Dict[str, Any]]:
    sym = str(position.get("symbol", "") or "").upper()
    side = _pos_direction(position.get("side"))
    if not sym or not side:
        return None
    try:
        from backend.services.paper_trading_engine import paper_engine
        res = paper_engine.close_position(
            db, account_id, sym, side, reason=str(reason)[:120],
            strategy_id=position.get("strategy_id"),
        )
        if res:
            _pnl = res.get("pnl", 0) if isinstance(res, dict) else 0
            # reduce_count 记账：持仓管理减仓此前未 +1，导致统计口径缺失。
            try:
                from datetime import datetime as _dt, timezone as _tz
                from backend.database.models import PaperPosition as _PPos
                _pid = position.get("id")
                if _pid:
                    _row = db.query(_PPos).filter(_PPos.id == int(_pid)).first()
                    if _row is not None:
                        _row.reduce_count = int(getattr(_row, "reduce_count", 0) or 0) + 1
                        _row.last_reduce_at = _dt.now(_tz.utc)
                        db.commit()
            except Exception as _rc_err:
                logger.debug("[MidLong] stage=manage %s reduce_count 更新失败: %s", sym, _rc_err)
            host.append_event(
                session, "pos_mgmt_close",
                f"🚪 [持仓管理] {sym}[{side}] 离场: {reason} | PnL=${_pnl:+.2f}",
            )
            logger.info(
                "[MidLong] stage=manage symbol=%s action=close reason=%s pnl=%s",
                sym, reason, _pnl,
            )
            return res
        logger.info("[MidLong] stage=manage %s close 被 gate 拦截或已平: %s", sym, reason)
    except Exception as e:
        logger.warning("[MidLong] stage=manage %s 平仓执行失败: %s", sym, e)
    return None


def _exec_reduce(db, *, account_id, position, ratio: float, reason: str, host, session) -> Optional[Dict[str, Any]]:
    sym = str(position.get("symbol", "") or "").upper()
    side = _pos_direction(position.get("side"))
    if not sym or not side:
        return None
    qty = float(position.get("size", 0) or position.get("quantity", 0) or 0)
    _qty = round(qty * ratio, 8)
    if _qty <= 0:
        return None
    try:
        from backend.services.paper_trading_engine import paper_engine
        res = paper_engine.close_position(
            db, account_id, sym, side, reason=str(reason)[:100],
            quantity=_qty, strategy_id=position.get("strategy_id"),
        )
        if res:
            _pnl = res.get("pnl", 0) if isinstance(res, dict) else 0
            host.append_event(
                session, "pos_mgmt_reduce",
                f"✂️ [持仓管理] {sym}[{side}] 减仓{ratio:.0%}: {reason} | PnL=${_pnl:+.2f}",
            )
            logger.info(
                "[MidLong] stage=manage symbol=%s action=reduce ratio=%.0f%% reason=%s pnl=%s",
                sym, ratio * 100, reason, _pnl,
            )
            return res
    except Exception as e:
        logger.warning("[MidLong] stage=manage %s 减仓执行失败: %s", sym, e)
    return None


def _exec_tighten(db, *, account_id, position, new_sl, host, session, tp_price=None) -> bool:
    pid = position.get("id")
    if not pid:
        return False
    # [P0-2] 浮盈 tighten 保护：保证金口径浮盈 > 1.5% 时，收紧的 SL 不得越过
    # entry±1%（价格），防止微利仓被推进的保本线过早收割
    # （id=2641 peak 2.32% 被推进到 entry+1.47% 的 SL 扫掉）。阈值可经 env 覆盖。
    try:
        entry = float(position.get("entry_price", 0) or 0)
        if entry > 0 and _pnl_pct_of(position) > _cfg_float("MIDLONG_TIGHTEN_PROFIT_FLOOR", 0.015):
            _sl_floor = entry * (1 + _cfg_float("MIDLONG_TIGHTEN_SL_FLOOR", 0.01))
            _sl_cap = entry * (1 - _cfg_float("MIDLONG_TIGHTEN_SL_FLOOR", 0.01))
            _sl = float(new_sl or 0)
            side = _pos_direction(position.get("side"))
            if side == "long" and _sl < _sl_floor:
                logger.info(
                    "[MidLong] stage=manage %s tighten SL %.6f < entry+1%%=%.6f → 抬到 %.6f（浮盈保护）",
                    str(position.get("symbol", "") or "").upper(), _sl, _sl_floor, _sl_floor,
                )
                new_sl = round(_sl_floor, 6)
            elif side == "short" and _sl > _sl_cap:
                logger.info(
                    "[MidLong] stage=manage %s tighten SL %.6f > entry-1%%=%.6f → 压到 %.6f（浮盈保护）",
                    str(position.get("symbol", "") or "").upper(), _sl, _sl_cap, _sl_cap,
                )
                new_sl = round(_sl_cap, 6)
    except Exception as _te:
        logger.debug("[MidLong] stage=manage pid=%s tighten 浮盈保护计算异常: %s", pid, _te)
    try:
        from backend.services.paper_trading_engine import paper_engine
        ok = paper_engine.update_position_tp_sl(
            db, int(pid), tp_price=tp_price, sl_price=new_sl,
        )
        if ok:
            sym = str(position.get("symbol", "") or "").upper()
            host.append_event(
                session, "pos_mgmt_tighten",
                f"🎯 [持仓管理] {sym} 收紧SL→{new_sl:.6f}" + (f" TP→{tp_price:.6f}" if tp_price else ""),
            )
            logger.info("[MidLong] stage=manage symbol=%s action=tighten new_sl=%s", sym, new_sl)
        return bool(ok)
    except Exception as e:
        logger.warning("[MidLong] stage=manage pid=%s TP/SL 调整失败: %s", pid, e)
        return False


def _exec_pyramid(
    db, *, account_id, position: Dict[str, Any],
    market_summary: Dict[str, Any], host, session, trading_mode: str,
) -> bool:
    """滚仓执行：5 层门控在 position_manager.evaluate_pyramid 内部，通过才下单。"""
    sym = str(position.get("symbol", "") or "").upper()
    side = _pos_direction(position.get("side"))
    if not sym or not side:
        return False
    tier = _tier_of(position)
    try:
        from backend.services.paper_trading_engine import paper_engine
        from backend.services.position_memory_manager import position_manager

        # 仅浮盈滚仓（产品决策 §7.4：浮亏加仓=自杀）
        pnl_pct = _pnl_pct_of(position)
        if _cfg_bool("MIDLONG_POSITION_MGMT_PYRAMID_ONLY_PROFIT", True) and pnl_pct <= 0:
            host.append_event(
                session, "pos_mgmt_pyramid_skip",
                f"📊 [持仓管理] {sym} 滚仓跳过: 浮亏({pnl_pct:+.1%})禁止加仓",
            )
            return False

        _gate_ms = _build_gate_market_summary(market_summary, sym)
        plan = position_manager.evaluate_pyramid(
            db=db, account_id=account_id, symbol=sym, side=side,
            ai_confidence=0.60,  # LLM 已判 add；0.60>PYRAMID_MIN_CONFIDENCE=0.35
            current_price=float(position.get("mark_price", 0) or 0),
            existing_position=position,
            volatility_pct=0.015,
            tier=tier,
            market_summary=_gate_ms,
        )
        if plan.action != "pyramid":
            host.append_event(
                session, "pos_mgmt_pyramid_skip",
                f"📊 [持仓管理] {sym} 滚仓门控拦截: {getattr(plan, 'reasoning', '') or 'no_reason'}",
            )
            return False

        mark = float(position.get("mark_price", 0) or 0)
        qty = plan.notional_usd / mark if (mark and mark > 0) else 0
        if qty <= 0:
            return False
        result = paper_engine.place_order(
            db, account_id, sym,
            "buy" if side == "long" else "sell",
            quantity=qty, leverage=float(position.get("leverage", 10) or 10),
            tp_price=plan.take_profit_price, sl_price=plan.stop_loss_price,
            strategy_id=position.get("strategy_id"),
            timeframe_tier=tier,
            trade_nature=position.get("trade_nature"),
            add_type="pyramid",
        )
        if result and result.get("status") == "filled":
            host.append_event(
                session, "pos_mgmt_pyramid",
                f"📈 [持仓管理] 顺势滚仓 {sym}[{side}] +${plan.margin_usd:.0f} | {getattr(plan, 'reasoning', '') or ''}",
            )
            logger.info(
                "[MidLong] stage=manage symbol=%s action=pyramid margin=%.0f qty=%s",
                sym, plan.margin_usd or 0, qty,
            )
            return True
        logger.info("[MidLong] stage=manage %s 滚仓下单未成交", sym)
    except Exception as e:
        logger.warning("[MidLong] stage=manage %s 滚仓执行异常: %s", sym, e)
    return False


# ──────────────────────────────────────────────────────────────────────
# 模式 B 主入口
# ──────────────────────────────────────────────────────────────────────
def manage_position(
    db,
    *,
    host,
    session,
    account_id: int,
    symbol: str,
    position: Dict[str, Any],
    market_summary: Dict[str, Any],
    analyst_reports: Dict[str, Any],
    trading_mode: str,
) -> Dict[str, Any]:
    """模式 B：对单个已持仓交易对做六维仓位发展分析并执行。

    每 tick 最多执行一个实质动作，优先级：close > add(pyramid) > tighten > reduce(仅浮亏) > hold。
    返回决策摘要 dict（供 _trend_one 组装事件与日志）。
    """
    sym = str(symbol or "").upper()
    _key = f"{account_id}:{sym}"
    _out = {
        "action": "hold", "score": 0, "direction": "manage",
        "reasoning": "", "hold_reason": "pos_mgmt_hold",
    }

    if not _cfg_bool("MIDLONG_POSITION_MGMT_ENABLED", True):
        return _out

    # position 未由调用方传入时，自动拉取该 symbol 的未平仓中长线仓位
    if not position:
        _positions = _open_midlong_positions(db, account_id)
        position = next(
            (p for p in _positions if str(p.get("symbol") or "").upper() == sym), {},
        )
    if not position:
        return _out

    # ── 全局节流：MIDLONG_POSITION_MGMT_INTERVAL_SEC（0=随 tick）──
    _interval = _cfg_int("MIDLONG_POSITION_MGMT_INTERVAL_SEC", 0)
    _now = time.time()
    if _interval > 0:
        _last = _last_global_run_ts.get(_key, 0.0)
        if (_now - _last) < _interval:
            return _out
        _last_global_run_ts[_key] = _now

    # ── 持仓上下文 ──
    side = _pos_direction(position.get("side"))
    pnl_pct = _pnl_pct_of(position)
    hold_hours = _held_hours(position, db)
    pos_tier = _tier_of(position)

    # 日志/事件：六维信号摘要（§7.7）
    _sig = {
        "direction": "pending", "pyramid": "pending", "review": "pending",
        "staged_tp": "pending", "exit": "pending",
    }

    def _summary(reason: str, action: str = "hold") -> Dict[str, Any]:
        _out["action"] = action
        _out["reasoning"] = reason
        if action == "hold":
            _out["hold_reason"] = reason
        return _out

    # ═══ ⑥ 反转 / 无进展离场（规则，每 tick）═══
    rev = _dim_reversal(position, market_summary)
    _sig["exit"] = rev["channel"] or "no"
    if rev["action"] == "close":
        _exec_close(db, account_id=account_id, position=position,
                    reason=rev["reason"], host=host, session=session)
        logger.info(
            "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
            "direction=skipped pyramid=skipped review=skipped staged_tp=skipped exit=%s reason=%s",
            sym, side, pnl_pct * 100, hold_hours, rev["channel"], rev["reason"],
        )
        return _summary(f"反转离场: {rev['reason']}", action="manage_close")

    # ═══ ⑤ 分批止盈（规则，每 tick）═══
    staged = _dim_staged_tp(db, host=host, session=session, account_id=account_id,
                            position=position, market_summary=market_summary)
    _sig["staged_tp"] = staged["channel"] or "no"
    if staged["action"] == "reduce":
        _exec_reduce(db, account_id=account_id, position=position,
                     ratio=staged["ratio"], reason=staged["reason"], host=host, session=session)
        logger.info(
            "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
            "direction=skipped pyramid=skipped review=skipped staged_tp=%s exit=no reason=%s",
            sym, side, pnl_pct * 100, hold_hours, staged["channel"], staged["reason"],
        )
        return _summary(f"分批止盈{staged['channel']}: {staged['reason']}", action="manage_reduce")
    if staged["action"] == "close":
        _exec_close(db, account_id=account_id, position=position,
                    reason=staged["reason"], host=host, session=session)
        logger.info(
            "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh exit=%s reason=%s",
            sym, side, pnl_pct * 100, hold_hours, staged["channel"], staged["reason"],
        )
        return _summary(f"追踪止损触发: {staged['reason']}", action="manage_close")

    # ═══ LLM 维度（①②③）节流：复用 exit_state_json.last_trend_review_ts ═══
    _llm_interval = _cfg_int("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", 900)
    _last_llm = _last_llm_run_ts.get(_key, 0.0)
    _llm_due = (_now - _last_llm) >= _llm_interval
    _db_pos = None
    _state: Dict[str, Any] = {}
    pid = position.get("id")
    if pid and db is not None:
        try:
            from backend.database.models import PaperPosition
            _db_pos = db.query(PaperPosition).filter(PaperPosition.id == int(pid)).first()
            if _db_pos is not None:
                try:
                    _state = json.loads(getattr(_db_pos, "exit_state_json", None) or "{}")
                except Exception:
                    _state = {}
                # 与 run_trend_review 同 key：模式 B 接管后 90min 兜底自动休眠
                _last_llm = max(_last_llm, float(_state.get("last_trend_review_ts", 0) or 0))
                _llm_due = (_now - _last_llm) >= _llm_interval
        except Exception as _e:
            logger.debug("[MidLong] stage=manage %s 读取 exit_state 失败: %s", sym, _e)

    if not _llm_due:
        logger.debug("[MidLong] stage=manage %s LLM维度节流中(距上次%.0fs)", sym, _now - _last_llm)
        return _summary(f"规则维度已检查({_sig['exit']}/{_sig['staged_tp']})，LLM维度节流中", action="manage_hold")

    # ═══ ① + ③ 方向延续性复查 + TP/SL 调整（LLM）═══
    try:
        review = _dim_direction(
            db, account_id=account_id, symbol=sym, position=position,
            market_summary=market_summary, analyst_reports=analyst_reports,
        )
        _review_action = str(review.get("action") or "hold").lower()
        _sig["review"] = _review_action
        _sig["direction"] = "valid" if _review_action in ("hold", "tighten_trailing") else _review_action
    except Exception as e:
        logger.warning("[MidLong] stage=manage %s 方向复查异常: %s", sym, e)
        review = {"action": "hold", "reasoning": f"err:{e}"}
        _review_action = "hold"

    # ═══ ② 滚仓（LLM，随①同轮执行）═══
    try:
        pyr = _dim_pyramid(
            db, account_id=account_id, symbol=sym, position=position,
            market_summary=market_summary, analyst_reports=analyst_reports,
        )
        _pyr_action = str(pyr.get("action") or "skip").lower()
        _sig["pyramid"] = _pyr_action
    except Exception as e:
        logger.warning("[MidLong] stage=manage %s 滚仓判断异常: %s", sym, e)
        pyr = {"action": "skip", "reasoning": f"err:{e}"}
        _pyr_action = "skip"

    _last_llm_run_ts[_key] = _now
    _reason_base = str(review.get("reasoning") or "")[:200]

    # ═══ 决策合并（单一优先级：close > pyramid(add) > tighten > reduce[仅浮亏] > hold）═══
    if _review_action == "close":
        _exec_close(db, account_id=account_id, position=position,
                    reason=f"trend_broken: {_reason_base}", host=host, session=session)
        logger.info(
            "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
            "direction=broken pyramid=%s review=close staged_tp=%s exit=no reason=%s",
            sym, side, pnl_pct * 100, hold_hours, _pyr_action, _sig["staged_tp"], _reason_base,
        )
        return _summary(f"方向破坏离场: {_reason_base}", action="manage_close")

    # [P1-1] 滚仓优先于减仓：
    # ① LLM 判 add 且浮盈 → 立即进 5 层门控；
    # ② 规则直通：保证金浮盈 > MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL(默认5%) 且
    #    方向 valid(hold/tighten_trailing) → 跳过 LLM wait 直接进 5 层门控。
    _pyr_direct = (
        pnl_pct > _cfg_float("MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL", 0.05)
        and _review_action in ("hold", "tighten_trailing")
    )
    if (_pyr_action == "add" and pnl_pct > 0) or _pyr_direct:
        _ok = _exec_pyramid(
            db, account_id=account_id, position=position,
            market_summary=market_summary, host=host,
            session=session, trading_mode=trading_mode,
        )
        if _ok:
            _why = "规则直通" if (_pyr_direct and _pyr_action != "add") else "LLM add"
            return _summary(f"顺势滚仓执行({_why}): {pyr.get('reasoning') or ''}", action="manage_pyramid")

    if _review_action == "tighten_trailing":
        _trend_adj = review.get("trend_adjustment") or {}
        _atr_mult = float(_trend_adj.get("trailing_atr_mult") or 0)
        _new_sl = None
        if _atr_mult > 0:
            mark = float(position.get("mark_price", 0) or position.get("entry_price", 0) or 0)
            _sym_mkt = market_summary.get(sym) or {}
            _atr_pct = 0.02
            if isinstance(_sym_mkt, dict):
                _atr_pct = float(_sym_mkt.get("volatility_value", 0.02) or 0.02)
            _band = mark * _atr_pct * _atr_mult
            if side == "long":
                _new_sl = mark - _band
            else:
                _new_sl = mark + _band
        _tight_ok = False
        if _new_sl and _new_sl > 0:
            _tight_ok = _exec_tighten(db, account_id=account_id, position=position,
                                      new_sl=round(_new_sl, 6), host=host, session=session)
        if _tight_ok:
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
                "direction=strong pyramid=%s review=tighten staged_tp=%s exit=no reason=%s",
                sym, side, pnl_pct * 100, hold_hours, _pyr_action, _sig["staged_tp"], _reason_base,
            )
            return _summary(f"收紧追踪止损 SL→{_new_sl:.6f}: {_reason_base}", action="manage_tighten")

    # [P1-2] 减仓不对称治理：浮盈时 LLM reduce 降级为 hold（趋势未破坏不砍盈利仓），
    # 仅浮亏或平盘时允许执行减仓。
    if _review_action == "reduce":
        if pnl_pct <= 0:
            _exec_reduce(db, account_id=account_id, position=position,
                         ratio=float(review.get("reduce_ratio", 0.3) or 0.3),
                         reason=f"trend_weaken: {_reason_base}", host=host, session=session)
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
                "direction=weaken pyramid=%s review=reduce staged_tp=%s exit=no reason=%s",
                sym, side, pnl_pct * 100, hold_hours, _pyr_action, _sig["staged_tp"], _reason_base,
            )
            return _summary(f"趋势减弱减仓: {_reason_base}", action="manage_reduce")
        logger.info(
            "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
            "direction=weaken pyramid=%s review=reduce→hold(浮盈保护) staged_tp=%s exit=no reason=%s",
            sym, side, pnl_pct * 100, hold_hours, _pyr_action, _sig["staged_tp"], _reason_base,
        )

    # ═══ 更新趋势复查时间戳 + trend_adjustment ═══
    try:
        _state["last_trend_review_ts"] = _now
        _trend_adj = review.get("trend_adjustment") or {}
        if _trend_adj:
            _state["trend_adjustment"] = _trend_adj
        if _db_pos is not None:
            from backend.services.position_exit_state import dump_exit_state
            _db_pos.exit_state_json = dump_exit_state(_state)
            db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug("[MidLong] stage=manage %s 复查时间戳写入失败: %s", sym, e)

    logger.info(
        "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
        "direction=%s pyramid=%s review=%s staged_tp=%s exit=%s reason=%s",
        sym, side, pnl_pct * 100, hold_hours,
        _sig["direction"], _pyr_action, _review_action, _sig["staged_tp"], _sig["exit"],
        _reason_base or "hold",
    )
    return _summary(_reason_base or "持仓管理分析完成，继续持有", action="manage_hold")


def run_position_management_for_session(
    db,
    *,
    host,
    session,
    account_id: int,
    symbols,
    market_summary: Dict[str, Any],
    analyst_reports: Dict[str, Any],
    trading_mode: str,
) -> Dict[str, Dict[str, Any]]:
    """批量入口：对账号内所有有仓的 symbol 执行模式 B。

    供独立 midlong 循环 / 其他调度点复用（当前 mlto_cycle._trend_one 按 symbol
    单仓调用 manage_position；本函数是聚合版本，方便未来把持仓管理从 TrendAgent
    并行流中拆出独立调度）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    sym_set = {str(s or "").upper() for s in (symbols or []) if s}
    positions = _open_midlong_positions(db, account_id)
    targets = [p for p in positions if (str(p.get("symbol") or "").upper()) in sym_set]
    if not targets:
        return {}

    results: Dict[str, Dict[str, Any]] = {}

    def _run_one(p: Dict[str, Any]) -> tuple:
        sym_u = str(p.get("symbol") or "").upper()
        # 每 symbol 独立 DB 连接，避免线程共享 SQLAlchemy session
        from backend.database.connection import SessionLocal
        _db = SessionLocal()
        try:
            return sym_u, manage_position(
                _db, host=host, session=session, account_id=account_id,
                symbol=sym_u, position=dict(p), market_summary=market_summary or {},
                analyst_reports=analyst_reports or {}, trading_mode=trading_mode,
            )
        finally:
            try:
                _db.close()
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max(1, min(4, len(targets)))) as pool:
        futs = {pool.submit(_run_one, p): p for p in targets}
        for fut in as_completed(futs):
            try:
                sym_u, dec = fut.result()
                results[sym_u] = dec
            except Exception as e:
                logger.debug("[MidLong] stage=manage 批量执行异常: %s", e)
    return results
