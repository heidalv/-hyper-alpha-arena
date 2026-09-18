# -*- coding: utf-8 -*-
"""trend_e1_engine — E1 主流 8 币趋势 sleeve 的**日任务执行器**（v3 方向 1，p1-trend-engine，2026-09-03）。

规则唯一源 = backend/services/trend_core.py（与 backend/research/trend_sleeve_backtest.py 回测同核）：
    入场 = 收盘 > EMA200 且 EMA20 > EMA50 > EMA100；出场 = 规则失效 或 收盘 < Chandelier(最高收盘 − 3×ATR20)
    定仓 = vol-target 35%（PositionConstruction 趋势车道再套帽：单币 ≤35%、单笔风险 ≤1.25%、簇 ≤50%、杠杆 ≤3x）
    触发 = 日线收盘（00:05 UTC 之后跑一次；exec_lag_days=1 与回测口径一致）

每天做四件事（幂等，可重复跑）：
  1. targets = target_positions_today()          → 8 币"今天应持 / 应空 + 目标权重 + Chandelier 止损"
  2. 对每个 E1 账户（TREND_E1_ACCOUNT_IDS）：
       应持而未持  → PositionConstruction.construct(lane=long, base_weight=目标权重) → paper_engine.place_order
                     （tier=long / nature=trend_follow / entry_source=trend_e1 / sl=Chandelier / structural_stop_price）
       应空而持有  → paper_engine.close_position(reason=trend_e1:rule_exit)
       都持有      → Chandelier 上移则 tighten（只上移，不下调）；权重偏差 > rebalance_tol 且方向为减 → 减仓到目标
  3. compute_trend_drift(account)               → backend/data/trend_drift/latest.json（验收：trend_drift=0）
  4. job_registry 心跳 + 摘要日志

E1 仓位标记：exit_state_json.entry_source = "trend_e1"（open 时经 position_metadata.entry_source 写入）。
带此标记的仓位：
  - long_trend_v2.manage_long_position（midlong 循环）跳过，不再用 L1 五票/周线 Chandelier 2.0 双重管理；
  - paper 引擎 max_hold 复审 / 兜底强平跳过（趋势仓的时间不是屏障，唯一出场 = 规则失效 / Chandelier）；
  - ExitPolicy(long) 声明：无 TP / 无 time_limit / 无递减 ROI，structural_stop=chandelier（价在 pos.sl_price）。

长车道独占（TREND_E1_LONG_LANE_EXCLUSIVE，默认随 TREND_E1_ENABLED）：启用后 paper place_order 拒绝非 E1 来源的
tier=long 新开仓（LLM 中长线流的 long 开仓从此只是"提议"，由 E1 规则决定要不要持有），存量非 E1 长仓仍由
long_trend_v2 管到自然退出——这样 trend_drift 才会收敛到 0。

开关：
  TREND_E1_ENABLED            默认 false（开了才会下单；关着只算目标 + 漂移）
  TREND_E1_ACCOUNT_IDS        逗号分隔的模拟账户 id（空 = 观察模式）
  TREND_E1_BUCKET_FRACTION    趋势桶占账户权益比例（E4 三桶 60/30/10 → 0.60）
  TREND_E1_REBALANCE_TOL      权重偏差超过多少才减仓到目标（默认 0.25 = 相对 25%）
  TREND_E1_DRY_RUN            true 时只打印将要做的动作，不下单
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

E1_ENTRY_SOURCE = "trend_e1"
E1_TIER = "long"
E1_NATURE = "trend_follow"


# ────────────────────────────── 配置 ──────────────────────────────

def _flag(key: str, default: bool) -> bool:
    return str(os.getenv(key, "true" if default else "false")).strip().lower() in ("1", "true", "yes", "on")


def e1_enabled() -> bool:
    return _flag("TREND_E1_ENABLED", False)


def long_lane_exclusive() -> bool:
    """E1 启用时默认独占长车道（非 E1 的 tier=long 新开仓被拒）。"""
    raw = os.getenv("TREND_E1_LONG_LANE_EXCLUSIVE")
    if raw is None or str(raw).strip() == "":
        return e1_enabled()
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def e1_account_ids() -> List[int]:
    raw = (os.getenv("TREND_E1_ACCOUNT_IDS", "") or "").strip()
    out: List[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = int(float(tok))
            if v > 0 and v not in out:
                out.append(v)
        except Exception:
            continue
    return out


def bucket_fraction() -> float:
    try:
        return max(0.05, min(1.0, float(os.getenv("TREND_E1_BUCKET_FRACTION", "0.60"))))
    except Exception:
        return 0.60


def rebalance_tol() -> float:
    try:
        return max(0.05, float(os.getenv("TREND_E1_REBALANCE_TOL", "0.25")))
    except Exception:
        return 0.25


def dry_run() -> bool:
    return _flag("TREND_E1_DRY_RUN", False)


def adopt_legacy() -> bool:
    return _flag("TREND_E1_ADOPT_LEGACY_LONGS", True)


# ────────────────────────────── 仓位标记 ──────────────────────────────

def _exit_state(pos: Any) -> Dict[str, Any]:
    """兼容 ORM（exit_state_json 文本）与 get_positions dict（exit_state 字典）。"""
    try:
        if isinstance(pos, dict):
            es = pos.get("exit_state")
            if isinstance(es, dict):
                return es
            raw = pos.get("exit_state_json")
        else:
            raw = getattr(pos, "exit_state_json", None)
        if isinstance(raw, dict):
            return raw
        if raw:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def is_e1_position(pos: Any) -> bool:
    """exit_state_json.entry_source == trend_e1（或 metadata_json.entry_source）。"""
    es = _exit_state(pos)
    if str(es.get("entry_source") or "").strip().lower() == E1_ENTRY_SOURCE:
        return True
    try:
        raw = pos.get("metadata_json") if isinstance(pos, dict) else getattr(pos, "metadata_json", None)
        if raw:
            md = json.loads(raw) if isinstance(raw, str) else raw
            return str((md or {}).get("entry_source") or "").strip().lower() == E1_ENTRY_SOURCE
    except Exception:
        pass
    return False


def is_e1_metadata(position_metadata: Optional[Dict[str, Any]]) -> bool:
    return bool(position_metadata) and str(position_metadata.get("entry_source") or "").strip().lower() == E1_ENTRY_SOURCE


def long_lane_open_allowed(timeframe_tier: Optional[str], trade_nature: Optional[str],
                           position_metadata: Optional[Dict[str, Any]], add_type: Optional[str] = None,
                           symbol: Optional[str] = None,
                           session_id: Optional[str] = None) -> tuple[bool, str]:
    """paper place_order 收口处调用：E1 独占长车道时，非 E1 来源的 tier=long 新开仓被拒（加仓/平仓不管）。

    [调研轮10 2026-09-16] **AI 长线选币窄口径例外**：E1 独占会让「AI 选出来的长线标的」
    永远只记为提议、开不出仓（用户目标是中/长线 AI 选币都要能工作）。
    故当 `symbol` 属于会话 AI 长线池（AI 选币产出）时放行，**且只放行这一种来源**：
    非 E1 且非 AI 的 long 新开仍一律拒绝。
    回滚：`TREND_E1_LONG_LANE_AI_EXCEPTION=false`。
    """
    if not long_lane_exclusive():
        return True, ""
    if add_type in ("reduce", "close"):
        return True, ""
    from backend.services.position_construction import normalize_lane

    if normalize_lane(timeframe_tier, trade_nature) != "long":
        return True, ""
    if is_e1_metadata(position_metadata):
        return True, ""
    # AI 选币标的例外（默认开；判定失败按"非 AI"处理=保持原独占行为）
    try:
        _ai_exc = str(os.getenv("TREND_E1_LONG_LANE_AI_EXCEPTION", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        _ai_exc = True
    if _ai_exc and symbol:
        try:
            from backend.services.auto_coin_selector import is_auto_coin_symbol as _iacs
            if _iacs(str(symbol).upper(), session_id):
                return True, "AI 长线选币标的（E1 独占例外）"
        except Exception:
            pass
    return False, "长车道由 E1 趋势引擎独占（TREND_E1_LONG_LANE_EXCLUSIVE）：非 E1 来源的 long 新开仓仅记为提议"


# ────────────────────────────── 日任务 ──────────────────────────────

def _base(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "").replace("/", "").replace(":", "")
    for suf in ("USDT", "USDC", "USD", "PERP"):
        while s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    return s


def _account_symbol(db, account_id: int, base: str) -> str:
    """账户内已有仓位用什么符号格式就跟什么（默认 base，如 BTC）。"""
    try:
        from sqlalchemy import text

        row = db.execute(text("SELECT symbol FROM paper_positions WHERE account_id = :a ORDER BY id DESC LIMIT 1"),
                         {"a": account_id}).first()
        if row and row[0]:
            s = str(row[0]).upper()
            if s.endswith("USDT"):
                return f"{base}USDT"
    except Exception:
        pass
    return base


def _open_long_positions(db, account_id: int) -> List[Dict[str, Any]]:
    from backend.services.paper_trading_engine import paper_engine

    out = []
    for p in paper_engine.get_positions(db, account_id, status="open") or []:
        if str(p.get("side") or "").lower() != "long":
            continue
        tier = str(p.get("timeframe_tier") or "").lower()
        nature = str(p.get("trade_nature") or "").lower()
        if tier == E1_TIER or nature in ("trend_follow", "position"):
            out.append(p)
    return out


def _act(actions: List[Dict[str, Any]], kind: str, **kw) -> None:
    rec = {"action": kind, **kw}
    actions.append(rec)
    logger.info("[TrendE1] %s %s", kind, {k: v for k, v in kw.items() if k != "plan"})


def run_daily(*, account_ids: Optional[Sequence[int]] = None, execute: Optional[bool] = None,
              targets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """E1 日任务主入口。返回摘要（targets / 每账户动作 / 漂移）。"""
    from backend.core.tenant import set_system_identity
    from backend.database.connection import SessionLocal
    from backend.research.trend_sleeve_backtest import compute_trend_drift, target_positions_today
    from backend.services.trend_core import TrendRules

    started = datetime.now(timezone.utc)
    rules = TrendRules.from_env()
    tg = targets or target_positions_today(rules)
    accounts = list(account_ids) if account_ids is not None else e1_account_ids()
    do_exec = (e1_enabled() and not dry_run()) if execute is None else bool(execute)
    summary: Dict[str, Any] = {
        "started_at": started.isoformat(), "as_of_bar": tg.get("as_of_bar"), "exec_lag_days": tg.get("exec_lag_days"),
        "n_target_positions": tg.get("n_target_positions"), "gross_weight": tg.get("gross_weight"),
        "targets": {s: {"target": t.get("target"), "weight": t.get("target_weight"), "stop": t.get("chandelier_stop"),
                        "close": t.get("close"), "reason": t.get("reason")} for s, t in (tg.get("targets") or {}).items()},
        "execute": do_exec, "accounts": {},
    }
    if not accounts:
        summary["note"] = "观察模式：TREND_E1_ACCOUNT_IDS 为空，只算目标（不下单、不对账）"
        logger.info("[TrendE1] %s", summary["note"])
        return summary

    set_system_identity()
    for acct in accounts:
        db = SessionLocal()
        try:
            summary["accounts"][acct] = _run_account(db, acct, tg, rules, do_exec)
        except Exception as exc:
            logger.exception("[TrendE1] 账户 %s 执行异常: %s", acct, exc)
            summary["accounts"][acct] = {"error": str(exc)}
        finally:
            db.close()
        try:
            summary["accounts"][acct]["drift"] = _slim_drift(compute_trend_drift(acct, rules, targets=tg))
        except Exception as exc:
            logger.warning("[TrendE1] 漂移对账失败 acct=%s: %s", acct, exc)
    summary["elapsed_sec"] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
    _persist(summary)
    return summary


def _slim_drift(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: d.get(k) for k in ("trend_drift", "weight_drift", "leverage_violations", "missing", "as_of_bar")} | {
        "extra": [e.get("symbol") for e in (d.get("extra") or [])],
        "wrong_side": [e.get("symbol") for e in (d.get("wrong_side") or [])],
    }


def _run_account(db, account_id: int, tg: Dict[str, Any], rules, do_exec: bool) -> Dict[str, Any]:
    from sqlalchemy import text
    from backend.services import position_construction as pc
    from backend.services.paper_trading_engine import paper_engine

    actions: List[Dict[str, Any]] = []
    eq_row = db.execute(text("SELECT total_equity FROM paper_balances WHERE account_id = :a"), {"a": account_id}).first()
    equity = float(eq_row[0]) if eq_row and eq_row[0] is not None else 0.0
    sleeve_equity = equity * bucket_fraction()
    if equity <= 0:
        return {"equity": equity, "actions": [], "error": "账户权益为 0"}

    longs = _open_long_positions(db, account_id)
    by_base: Dict[str, List[Dict[str, Any]]] = {}
    for p in longs:
        by_base.setdefault(_base(p.get("symbol")), []).append(p)
    e1_by_base = {b: [p for p in ps if is_e1_position(p)] for b, ps in by_base.items()}
    other_long = {b: [p for p in ps if not is_e1_position(p)] for b, ps in by_base.items()}
    other_long = {b: ps for b, ps in other_long.items() if ps}

    tmap: Dict[str, Dict[str, Any]] = tg.get("targets") or {}
    core = list(tg.get("symbols") or [])
    lim = pc.LaneLimits.for_lane("long")

    # E1 车道在手名义（用于簇/gross 帽）
    def _notional(p: Dict[str, Any]) -> float:
        return float(p.get("size") or 0) * float(p.get("mark_price") or p.get("entry_price") or 0)

    # 存量接管（TREND_E1_ADOPT_LEGACY_LONGS，默认 true）：核心币且规则说"应持"的非 E1 长仓，
    # 由 E1 接管（打标 entry_source、止损抬到 Chandelier），后续按目标权重再平衡；规则说"应空"的存量仓
    # 不动（仍由 long_trend_v2 管到自然退出，漂移报告里记为 extra，交给人决定）。
    adopted: List[Dict[str, Any]] = []
    if do_exec and adopt_legacy():
        for s in core:
            t = tmap.get(s) or {}
            if not t.get("data_ok") or int(t.get("target") or 0) != 1 or e1_by_base.get(s) or not other_long.get(s):
                continue
            cand = sorted(other_long[s], key=_notional, reverse=True)[0]
            stop = t.get("chandelier_stop")
            if _adopt_position(db, int(cand.get("id") or 0), stop):
                e1_by_base[s] = [cand]
                other_long[s] = [p for p in other_long[s] if p is not cand] or []
                if not other_long[s]:
                    other_long.pop(s, None)
                adopted.append({"symbol": cand.get("symbol"), "position_id": cand.get("id"), "stop": stop})
                _act(actions, "adopt", symbol=cand.get("symbol"), position_id=cand.get("id"), stop=stop)
        if adopted:
            try:
                db.commit()
            except Exception:
                db.rollback()

    # 第一遍：先做平仓 / 减仓 / 收紧（释放余量），第二遍再开缺仓（用库内最新在手名义算帽）
    to_open: List[str] = []
    for s in core:
        t = tmap.get(s) or {}
        if not t.get("data_ok"):
            _act(actions, "skip", symbol=s, reason=t.get("reason"))
            continue
        want = int(t.get("target") or 0) == 1
        have = e1_by_base.get(s) or []
        stop = t.get("chandelier_stop")
        close = float(t.get("close") or 0)
        tw = float(t.get("target_weight") or 0.0)

        if want and not have:
            if tw <= 0 or close <= 0:
                _act(actions, "skip", symbol=s, reason=f"目标权重 {tw} / 价格 {close} 无效")
                continue
            to_open.append(s)
            continue

        if (not want) and have:
            for p in have:
                _act(actions, "close" if do_exec else "would_close", symbol=p.get("symbol"), position_id=p.get("id"),
                     reason=t.get("reason"))
                if do_exec:
                    paper_engine.close_position(db, account_id, p.get("symbol"), "long", reason="trend_e1:rule_exit",
                                                strategy_id=p.get("strategy_id"), position_id=int(p.get("id") or 0) or None)
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
            continue

        if want and have:
            for p in have:
                cur_sl = float(p.get("sl_price") or 0)
                if stop and float(stop) > cur_sl + 1e-9:
                    _act(actions, "tighten_sl" if do_exec else "would_tighten_sl", symbol=p.get("symbol"),
                         position_id=p.get("id"), old_sl=cur_sl, new_sl=float(stop))
                    if do_exec:
                        paper_engine.update_position_tp_sl(db, int(p.get("id") or 0), sl_price=float(stop))
                        _set_structural_stop(db, int(p.get("id") or 0), float(stop))
                        try:
                            db.commit()
                        except Exception:
                            db.rollback()
                # 权重超目标（相对 rebalance_tol）→ 减仓到目标；低于目标不追加（金字塔另议，避免追高）
                aw = _notional(p) / equity if equity > 0 else 0.0
                tw_acct = tw * bucket_fraction()
                if tw_acct > 0 and aw > tw_acct * (1 + rebalance_tol()):
                    reduce_qty = float(p.get("size") or 0) * (1 - tw_acct / aw)
                    _act(actions, "reduce" if do_exec else "would_reduce", symbol=p.get("symbol"), position_id=p.get("id"),
                         actual_weight=round(aw, 4), target_weight=round(tw_acct, 4), quantity=reduce_qty)
                    if do_exec and reduce_qty > 0:
                        paper_engine.close_position(db, account_id, p.get("symbol"), "long", reason="trend_e1:rebalance",
                                                    quantity=reduce_qty, strategy_id=p.get("strategy_id"),
                                                    position_id=int(p.get("id") or 0) or None)
                        try:
                            db.commit()
                        except Exception:
                            db.rollback()
            continue

        _act(actions, "flat_ok", symbol=s)

    # 第二遍：开缺仓。帽用库内最新在手名义（第一遍的减仓/平仓已提交）。
    for s in to_open:
        t = tmap.get(s) or {}
        stop = t.get("chandelier_stop")
        close = float(t.get("close") or 0)
        tw = float(t.get("target_weight") or 0.0)
        sym = _account_symbol(db, account_id, s)
        price = 0.0
        try:
            price = float(paper_engine._get_current_price(sym, "binance") or 0)
        except Exception:
            price = 0.0
        if price <= 0:
            price = close
        sd = ((close - float(stop)) / close) if (stop and close > 0 and float(stop) < close) else t.get("initial_stop_distance_pct")
        opens = pc.open_notionals(db, account_id, sym, lane="long")
        # [轮72] 读不到在手敞口 → 跳过开仓（不得按零敞口构造，否则帽额被整额叠加）
        if not opens.get("ok", False):
            _act(actions, "skip_open", symbol=s,
                 reason="在手敞口读取失败，跳过开仓（fail-closed）")
            continue
        plan = pc.construct(
            lane="long", symbol=sym, equity=sleeve_equity, price=price, realized_vol=t.get("realized_vol"),
            stop_distance_pct=sd, base_weight=tw, symbol_open_notional=opens["symbol"],
            cluster_open_notional=opens["cluster"], lane_open_notional=opens["lane"], limits=lim,
        )
        if not plan.ok:
            _act(actions, "skip_open", symbol=s, reason=f"PositionConstruction 无余量 {plan.caps_applied}",
                 lane_open=round(opens["lane"], 2), sleeve_equity=round(sleeve_equity, 2))
            continue
        # 止损：用目标里的 Chandelier；没有就按 stop_distance 反推
        sl = float(stop) if stop else (price * (1 - float(sd)) if sd else None)
        meta = {
            "entry_source": E1_ENTRY_SOURCE, "structural_stop_price": sl,
            "e1": {"as_of_bar": tg.get("as_of_bar"), "decision_bar": t.get("decision_bar"), "target_weight": tw,
                   "realized_vol": t.get("realized_vol"), "ema": t.get("ema"), "rules": tg.get("rules")},
        }
        _act(actions, "open" if do_exec else "would_open", symbol=sym, notional=round(plan.notional, 2),
             quantity=plan.quantity, leverage=plan.leverage, sl=sl, weight=round(plan.weight, 4), caps=plan.caps_applied)
        if do_exec:
            res = paper_engine.place_order(
                db, account_id, sym, "buy", float(plan.quantity), order_type="market", price=price,
                leverage=float(plan.leverage), sl_price=sl, tp_price=None, strategy_id=f"trend_e1:{s}",
                timeframe_tier=E1_TIER, trade_nature=E1_NATURE, position_metadata=meta,
            )
            ok = bool(res and res.get("success", True) and not res.get("blocked"))
            actions[-1]["result"] = {"ok": ok, "reason": (res or {}).get("reason") or (res or {}).get("error"),
                                     "order_id": (res or {}).get("order_id")}
            if ok:
                _tag_e1(db, account_id, sym)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            else:
                try:
                    db.rollback()
                except Exception:
                    pass

    # 非核心 E1 仓（规则集变更遗留）→ 平
    for b, ps in e1_by_base.items():
        if b in core:
            continue
        for p in ps:
            _act(actions, "close" if do_exec else "would_close", symbol=p.get("symbol"), position_id=p.get("id"),
                 reason="非核心币（TREND_CORE_SYMBOLS 已变更）")
            if do_exec:
                paper_engine.close_position(db, account_id, p.get("symbol"), "long", reason="trend_e1:non_core",
                                            strategy_id=p.get("strategy_id"), position_id=int(p.get("id") or 0) or None)
                try:
                    db.commit()
                except Exception:
                    db.rollback()

    return {
        "equity": equity, "sleeve_equity": round(sleeve_equity, 2), "bucket_fraction": bucket_fraction(),
        "adopted": adopted,
        "e1_positions": {b: [p.get("id") for p in ps] for b, ps in e1_by_base.items() if ps},
        "other_long_positions": {b: [p.get("id") for p in ps] for b, ps in other_long.items()},
        "actions": actions,
    }


def _tag_e1(db, account_id: int, symbol: str) -> None:
    """开仓后确保 exit_state_json.entry_source=trend_e1（place_order 会写 exit_policy 快照，这里补 entry_source）。"""
    try:
        from backend.database.models import PaperPosition

        pos = (db.query(PaperPosition).filter(PaperPosition.account_id == account_id, PaperPosition.symbol == symbol,
                                              PaperPosition.side == "long", PaperPosition.status == "open")
               .order_by(PaperPosition.id.desc()).first())
        if not pos:
            return
        es = _exit_state(pos)
        if es.get("entry_source") != E1_ENTRY_SOURCE:
            es["entry_source"] = E1_ENTRY_SOURCE
            pos.exit_state_json = json.dumps(es, ensure_ascii=False)
            db.flush()
    except Exception as exc:
        logger.debug("[TrendE1] 标记 entry_source 失败: %s", exc)


def _adopt_position(db, position_id: int, stop: Optional[float]) -> bool:
    """存量长仓接管：打 entry_source=trend_e1、写 structural_stop_price、止损只抬不降、清掉中长线分档状态。"""
    try:
        from backend.database.models import PaperPosition
        from backend.services.exit.exit_policy import ExitPolicy

        pos = db.query(PaperPosition).filter(PaperPosition.id == position_id, PaperPosition.status == "open").first()
        if not pos:
            return False
        es = _exit_state(pos)
        es["entry_source"] = E1_ENTRY_SOURCE
        es["adopted_by_e1_at"] = datetime.now(timezone.utc).isoformat()
        es["exit_policy"] = ExitPolicy.for_lane("long").to_dict()
        if stop and float(stop) > 0:
            es["structural_stop_price"] = float(stop)
            cur_sl = float(pos.sl_price or 0)
            if float(stop) > cur_sl:
                pos.sl_price = float(stop)
        # 趋势仓不设固定 TP（让利润奔跑；唯一出场 = 规则失效 / Chandelier）
        pos.tp_price = None
        pos.exit_state_json = json.dumps(es, ensure_ascii=False)
        db.flush()
        return True
    except Exception as exc:
        logger.warning("[TrendE1] 接管仓位 %s 失败: %s", position_id, exc)
        return False


def _set_structural_stop(db, position_id: int, stop: float) -> None:
    try:
        from backend.database.models import PaperPosition

        pos = db.query(PaperPosition).filter(PaperPosition.id == position_id).first()
        if not pos:
            return
        es = _exit_state(pos)
        es["structural_stop_price"] = float(stop)
        pos.exit_state_json = json.dumps(es, ensure_ascii=False)
        db.flush()
    except Exception as exc:
        logger.debug("[TrendE1] 写 structural_stop_price 失败: %s", exc)


def _persist(summary: Dict[str, Any]) -> None:
    try:
        from backend.research.trend_sleeve_backtest import DATA_DIR

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "e1_last_run.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        with open(os.path.join(DATA_DIR, "e1_runs.jsonl"), "a", encoding="utf-8") as f:
            slim = {k: summary.get(k) for k in ("started_at", "as_of_bar", "n_target_positions", "gross_weight", "execute", "elapsed_sec")}
            slim["accounts"] = {a: {"n_actions": len((v or {}).get("actions") or []), "drift": (v or {}).get("drift")}
                                for a, v in (summary.get("accounts") or {}).items()}
            f.write(json.dumps(slim, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("[TrendE1] 落盘失败: %s", exc)


def load_last_run() -> Optional[Dict[str, Any]]:
    try:
        from backend.research.trend_sleeve_backtest import DATA_DIR

        p = os.path.join(DATA_DIR, "e1_last_run.json")
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# [§51 2026-09-10] E1 实盘下单路径是否已实现：当前 **未实现**。
# 证据：`paper_engine.place_order` 全函数无 live 路由分支（`paper_trading_engine.py`
# 内不存在 `trading_mode` / `is_live` / `account_type` / `get_executor` 任何引用），
# 所有 E1 执行（含 do_exec=True）都是**模拟成交**。因此 `TREND_E1_LIVE_ASTER=true`
# 只是意图声明，不产生实盘。契约测试 `test_trend_e1_f4_pre_exec_20260910.py`
# 会断言本常量与代码事实一致；将来实现实盘路由时必须同时把本常量改 True，
# 并让 F4 门禁成为硬前置（否则该开关会变成静默无效的假开关）。
E1_LIVE_ROUTING_IMPLEMENTED = False


def _pre_exec_live_gate() -> Dict[str, Any]:
    """[§51 修复] 执行**前**的 F4 live 门禁（live 意图的唯一判定点）。

    修复前的三个静默失效（§51.1）：
      1. 门禁在 `run_daily()` **之后**才评估 → 结构上不可能拦住任何执行；
      2. 唯一被文档声明为 "live 路径调用" 的 `trend_e1_f4_gate.assert_live_allowed()`
         在三准则核查下零调用点（死包装），文档所声明的安全属性无人执行；
      3. `TREND_E1_LIVE_ASTER` 在整个执行链上没有任何消费方 → 打开该开关既不会
         带来实盘、也不会被拒绝，是一个**静默无效的假开关**（文档却声称"须 F4 过门"）。

    返回 {"live_requested", "allowed", "blocked", "gate", "reason"}。
    live 未请求时不评估门禁（不写 `f4_gate_latest.json`，保持既有 paper 路径零副作用）。
    fail-closed：门禁自身异常一律按"未过门"处理，绝不静默放行。
    """
    from backend.services.trend_e1_f4_gate import assert_live_allowed, live_aster_requested
    if not live_aster_requested():
        return {"live_requested": False, "allowed": False, "blocked": False,
                "gate": None, "reason": ""}
    try:
        res = assert_live_allowed()  # 内含 evaluate_f4_gate(persist=True)
        gate = res.get("gate") or {}
        if not res.get("allowed"):
            failed = ",".join(str(c.get("name")) for c in (gate.get("checks") or []) if not c.get("ok"))
            logger.warning(
                "[TrendE1] TREND_E1_LIVE_ASTER=true 但 F4 未过门（失败项: %s）→ 拒 live 意图，保持 paper",
                failed or "unknown")
            return {"live_requested": True, "allowed": False, "blocked": True,
                    "gate": gate, "reason": f"f4_failed:{failed or 'unknown'}"}
        if not E1_LIVE_ROUTING_IMPLEMENTED:
            logger.warning(
                "[TrendE1] F4 已过门，但本构建**没有** E1 实盘下单路径"
                "（place_order 仅落模拟成交）→ TREND_E1_LIVE_ASTER=true 不产生实盘，保持 paper")
            return {"live_requested": True, "allowed": True, "blocked": True,
                    "gate": gate, "reason": "live_routing_unimplemented"}
        return {"live_requested": True, "allowed": True, "blocked": False,
                "gate": gate, "reason": ""}
    except Exception as exc:
        logger.warning("[TrendE1] F4 门禁异常 → fail-closed 拒 live 意图: %s", exc)
        return {"live_requested": True, "allowed": False, "blocked": True,
                "gate": None, "reason": f"gate_error:{exc}"}


def scheduled_job() -> Dict[str, Any]:
    """APScheduler 入口（日线收盘后 00:20 UTC；job_registry 名 trend_e1_daily，由 v3_jobs_ext 的 wrap 负责心跳/失败登记）。"""
    # [§51 修复] 门禁**前移**：live 意图必须先过 F4 才能谈执行。
    # 修复前：`run_daily()` 先执行，之后才评估 F4 并把结论写进 note——
    # 结构上不可能拦住任何执行；且 `assert_live_allowed()` 零调用点（死包装）。
    lg = _pre_exec_live_gate()
    out = run_daily()
    if lg.get("blocked"):
        out["f4_blocked"] = True
        out["note"] = (out.get("note") or "") + (
            " | TREND_E1_LIVE_ASTER 未过 F4，保持 paper" if not lg.get("allowed")
            else " | F4 已过门但本构建无实盘路径，保持 paper")
    f4 = lg.get("gate")
    return {
        "as_of_bar": out.get("as_of_bar"), "n_target_positions": out.get("n_target_positions"),
        "gross_weight": out.get("gross_weight"), "execute": out.get("execute"), "note": out.get("note"),
        "f4": ({"passed": f4.get("passed"), "live_allowed": f4.get("live_allowed")} if f4 else None),
        "f4_live_requested": bool(lg.get("live_requested")),
        "f4_blocked": bool(lg.get("blocked")),
        "f4_reason": (lg.get("reason") or None),
        "accounts": {a: {"n_actions": len((v or {}).get("actions") or []), "drift": (v or {}).get("drift"),
                         "error": (v or {}).get("error")} for a, v in (out.get("accounts") or {}).items()},
    }


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="E1 趋势 sleeve 日任务")
    ap.add_argument("--accounts", default="", help="逗号分隔账户 id（默认 TREND_E1_ACCOUNT_IDS）")
    ap.add_argument("--execute", action="store_true", help="真的下单/平仓（默认按 TREND_E1_ENABLED 与 DRY_RUN）")
    ap.add_argument("--dry", action="store_true", help="只打印动作")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ids = [int(x) for x in args.accounts.split(",") if x.strip()] or None
    exec_flag = True if args.execute else (False if args.dry else None)
    out = run_daily(account_ids=ids, execute=exec_flag)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    sys.exit(0)
