"""中长线持仓管理模式（Phase 5）— 模式 B 六维仓位发展分析。

开仓后，分析大脑从「入场思维」切换到「持仓发展思维」：不再反复问
「要不要开新仓」，而是围绕已持仓交易对做仓位发展分析。

六维分析（设计文档 MIDLONG_V2_ARCHITECTURE_DESIGN §4.6 / 审计报告 §7.3）：
  ① 方向延续性   trend_agent.review_position          → hold / reduce / close / tighten
  ② 滚仓(金字塔)  trend_agent.evaluate_pyramid + 5层门控 → add / wait / skip
  ③ TP/SL 调整   review_position.trend_adjustment      → update_position_tp_sl
  ④ 补仓(DCA)    [2026-09-12 F40] 受控逆势补仓：论题同向有效 + 反转价格闸
                 噪音区 + 亏损带内（-2%~-8%）→ evaluate_dca 全门控补仓；
                 否则 skip（绝不在未反转行情里小亏全平）
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
from typing import Any, Dict, List, Optional, Tuple

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
        _v = getattr(settings, key, default)
        return 0 if _v is None else int(_v)
    except Exception:
        try:
            return int(os.getenv(key, str(default)))
        except Exception:
            return default


def _cfg_float(key: str, default: float) -> float:
    try:
        from backend.config import settings
        _v = getattr(settings, key, default)
        return default if _v is None else float(_v)
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

    [M2 2026-08-21] 内部改为 exists() 扫全行匹配（原实现 .first() 取任意
    一行再判 tier/nature——同币先扫到 scalp 行时中长线仓会被漏检）。
    开仓互锁请改用 has_open_position_of_nature（同 nature 才拦截）。
    """
    return any(
        has_open_position_of_nature(db, account_id, symbol, g)
        for g in ("mid", "long")
    )


# [M2 2026-08-21] nature 分组：mid=swing/tier-mid；long=trend_follow/position/tier-long。
# 中线与长线互锁改为「同 nature 才拦截」：有 long 不再锁死 mid 开仓（各自
# 受 portfolio_budget / 单币集中度约束），同币 scalp 也不参与任何组判定。
_MIDLONG_NATURE_GROUPS: Dict[str, frozenset] = {
    "mid": frozenset({"swing"}),
    "long": frozenset({"trend_follow", "position"}),
    # [2026-09-07] LLM 日内波段车道：short 组互锁（同币已有日内仓则不重复开）
    "short": frozenset({"intraday", "scalp"}),
}


def has_open_position_of_nature(db, account_id, symbol: str, nature_group: str) -> bool:
    """[M2 2026-08-21] 该交易对是否已有指定 nature 组的未平仓仓（exists() 判定）。

    nature_group: "mid"（swing / tier=mid）或 "long"（trend_follow/position / tier=long）。
    开仓互锁语义：中线开仓只被 mid 组拦截，长线开仓只被 long 组拦截。
    """
    if not account_id or db is None:
        return False
    sym_u = str(symbol or "").upper()
    group = str(nature_group or "").lower()
    natures = _MIDLONG_NATURE_GROUPS.get(group)
    if not natures:
        return False
    try:
        from backend.database.models import PaperPosition
        rows = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == sym_u,
                PaperPosition.status == "open",
            )
            .all()
        )
        for pos in rows:
            tier = str(getattr(pos, "timeframe_tier", "") or "").lower()
            nature = str(getattr(pos, "trade_nature", "") or "").lower()
            if nature in natures or tier == group:
                return True
        return False
    except Exception as e:
        logger.warning("[MidLong] 持仓互锁判定异常 %s(%s): %s", sym_u, group, e)
        return False


def _factor_invalidated_reason() -> "str | None":
    """[M1-B 2026-08-21] 因子失效最小判定：路由活跃因子数跌破 FACTOR_ROUTE_MIN_ACTIVE_FACTORS。

    过渡期实现（设计 §4.2 同 PR 约束的「最小 factor_invalidated」）：
    开仓来源系统（factor_route）整体失效即视为入场论点失效——活跃集不足
    门槛时路由自身也会 hold 停新开，存量因子仓据此离场，避免「路由停摆、
    旧仓无人管」。判定不可用（读不到活跃集）时返回 None 不动仓
    （SL/TP/时间离场仍在）。
    """
    try:
        from backend.services.factor_engine.midlong_active_factor_set import (
            midlong_active_factor_set,
        )
        n = len(midlong_active_factor_set.get_active_factors())
    except Exception as e:
        logger.debug("[MidLong] factor_invalidated 活跃集读取失败(不动仓): %s", e)
        return None
    _min = _cfg_int("FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 2)
    if n < _min:
        return f"活跃因子{n}<{_min}，因子路由信号系统失效"
    return None


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
            # [2026-09-07] 日内波段(short/intraday)纳入管理扫描（论题硬离场哨兵覆盖）
            if tier in ("mid", "long", "short") or nature in ("trend_follow", "swing", "position", "intraday"):
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
    if tier in ("mid", "long", "short"):
        return tier
    nature = str(position.get("trade_nature") or "").lower()
    if nature in ("trend_follow", "position"):
        return "long"
    if nature == "swing":
        return "mid"
    # [2026-09-07] 日内波段：nature=intraday → short（此前默认落 mid，
    # 导致日内仓的论题硬离场读的是中线论题——退出保护错配）
    if nature in ("intraday", "scalp"):
        return "short"
    return "mid"


def thesis_invalidation_semantics_ok(
    *, position: Dict[str, Any], ipx: float, side: str,
) -> Tuple[bool, str]:
    """[调研轮18 2026-09-16] **让失效价触发与失效条件原文的语义对齐**（少砍早单）。

    这不是"加一道门禁"，而是**修一处实现与规格不一致**：

    30 天 6 笔 `thesis_invalidation` 平仓，逐笔把 `exit_state_json.invalidation_condition`
    原文与实现对照 —— 原文写的都是**收盘**条件：

      * `日线/4h收盘跌破5.931且无法收回，则多头修复判断作废`
      * `日线收盘跌破705（1d EMA21）则HH→HL结构破坏`
      * `4h收盘放量跌破100.19（24h低点+100整数关口）…`

    而实现是 **mark 价一碰就平**（原 `resolve_thesis_hard_exit`）。逐笔回放结果：
    **6 笔里 4 笔的 1h 收盘仍在失效价之内**（= 原文条件从未成立就被平掉），
    其中 UNI 平仓价甚至**已在失效价之上**；这 4 笔出场后价格分别走高
    （24h 高 +10.9% / +3.8% / +5.5% / +3.8%）。另 2 笔（SOL/VIRTUAL）1h 收盘确实
    收破，出场正确（VIRTUAL 之后继续跌 −9.1%）。⇒ 按原文口径修，可少砍 4/6 早单，
    且**不动**真正破位的 2 笔。

    ## 对齐口径

    1. **剧烈破位不等收盘**：`|mark/ipx − 1| ≥ MIDLONG_THESIS_INV_ESCAPE_DEPTH_PCT`
       （默认 2%）⇒ 立即触发（原文的"无法收回"语义）；
    2. **收盘确认**：`MIDLONG_THESIS_INV_CONFIRM_TF`（默认 1h）**已收盘** K 线
       收在失效价之外 ⇒ 触发（与原文"收盘跌破"一致）；
    3. 否则本轮不触发，下一 tick 复核。

    ## 安全性

    不触发**不等于取消保护**：硬止损（轮15b 后 mid ≤2%）、追踪止损、分段止盈、
    组合风控全部照旧生效 —— 这里只是不再用"瞬时触碰"提前砍仓。异常时保持旧行为
    （触发），避免把风险敞口留在无人看管的状态。

    回滚：`MIDLONG_THESIS_INV_REQUIRE_CLOSE=false`。
    """
    try:
        from backend.config.settings import MIDLONG_THESIS_INV_REQUIRE_CLOSE as _on
    except Exception:
        _on = True
    if not _on:
        return True, "semantics_off(触碰即平)"
    try:
        mark = float(position.get("mark_price") or position.get("current_price") or 0)
        if mark <= 0 or ipx <= 0:
            return True, "no_mark"
        _side = str(side or "").lower()
        depth = (ipx - mark) / ipx if _side == "long" else (mark - ipx) / ipx
        try:
            from backend.config.settings import MIDLONG_THESIS_INV_ESCAPE_DEPTH_PCT as _esc
        except Exception:
            _esc = 0.02
        _esc = float(_esc if _esc is not None else 0.02)
        if _esc > 0 and depth >= _esc:
            return True, f"剧烈破位 depth={depth*100:.2f}%≥{_esc*100:.2f}%（不等收盘）"
        try:
            from backend.config.settings import MIDLONG_THESIS_INV_CONFIRM_TF as _tf
        except Exception:
            _tf = "1h"
        _tf = str(_tf or "1h")
        sym = str(position.get("symbol") or "").upper()
        try:
            from backend.services.market_data import get_kline_data
            rows = get_kline_data(sym, period=_tf, count=3) or []
        except Exception as _kerr:  # noqa: BLE001
            return True, f"kline_unavailable:{type(_kerr).__name__}"
        closed = 0.0
        if len(rows) >= 2:   # 末根可能仍在形成 ⇒ 取最后一根已收盘
            try:
                closed = float(rows[-2].get("close") or 0)
            except (TypeError, ValueError, IndexError):
                closed = 0.0
        if not closed:
            for r in rows:
                try:
                    c = float(r.get("close") or 0)
                except (TypeError, ValueError):
                    continue
                if c > 0:
                    closed = c
        if not closed:
            return True, "no_closed_bar"
        if (_side == "long" and closed < ipx) or (_side == "short" and closed > ipx):
            return True, f"{_tf}收盘={closed:.6f} 已破 {ipx:.6f}"
        return False, (f"条件未成立: mark={mark:.6f} 瞬时穿透 {depth*100:.2f}%"
                       f"（剧烈破位线 {_esc*100:.2f}% 未到）且 {_tf} 收盘={closed:.6f} "
                       f"未破 {ipx:.6f} —— 按原文『收盘跌破』本轮不触发")
    except Exception as _e:  # noqa: BLE001
        logger.warning("[MidLong] thesis_invalidation_semantics_ok 异常(保持旧行为): %s", _e)
        return True, "semantics_error_keep_old"


def thesis_should_close_allowed(db, *, position: Dict[str, Any], thesis, tier: str):
    """[调研轮20 2026-09-17] `should_close` 是否允许平仓 —— **全路径共用**的 F39 确认入口。

    ## 缺陷（实测）

    F39 确认闸（`thesis_should_close_confirmed`，2026-09-12 基于 163 笔亏损平仓的反事实）
    此前**只装在 `manage_position` 一条路径**上；而"每 tick 全仓哨兵"
    （`full_auto_trading_service` 里直接调 `resolve_thesis_hard_exit` 后 `close_position`）
    **完全没有确认**。生产日志佐证：近 24h `反转确认闸拦截 / thesis_close_blocked /
    should_close 等待价格确认 / should_close_recovered` **全部 0 次**，而 14 天里
    `thesis_should_close` 平仓 16 笔、均亏 **−8.04**（合计 −128.59）。

    ⇒ 同一个保护只装一条路径 = 等于没装。本函数把它收敛成**单一入口**，供两条路径共用。

    语义完全沿用 F39（不改判据）：价格已突破同向失效价 / 论题方向翻反 / min_hold 已满
    且仍浮亏 / 紧急亏损 —— 任一满足才允许平仓；否则 (False, 原因)，由调用方保留仓位、
    继续走其它保护（硬止损/追踪/分段止盈）。
    开关：`MIDLONG_THESIS_CLOSE_CONFIRM_ENABLED`（false = 回到无确认旧行为）。
    """
    try:
        from backend.config.settings import MIDLONG_THESIS_CLOSE_CONFIRM_ENABLED as _on
    except Exception:
        _on = True
    if not _on:
        return True, "f39_off"
    try:
        return thesis_should_close_confirmed(
            db, position=position, thesis=thesis, tier=tier,
            pnl_pct=_pnl_pct_of(position), hold_hours=_held_hours(position, db),
        )
    except Exception as _e:  # noqa: BLE001
        logger.warning("[MidLong] thesis_should_close_allowed 异常(fail-open 放行): %s", _e)
        return True, "f39_error_fail_open"


def resolve_thesis_hard_exit(
    session_id: str,
    position: Dict[str, Any],
) -> Optional[tuple]:
    """论题硬离场：返回 (reason, thesis_dto) 或 None。

    [2026-09-07] 从 manage_position 抽出，供 midlong 每 tick 哨兵复用。
    根因：should_close 曾只挂在「本批扫描币」的 manage_position 上，
    主脑已写 should_close=true 的 mid 仓（如 BTC）若不在 batch 就一直挂着。

    - should_close：有仓即可平（允许论题翻空去平多头）。
    - invalidation：仅论题方向与仓位同向时用失效价
      （空头论题的上沿失效价不得当成多头「跌破即平」）。
    """
    try:
        from backend.config.settings import midlong_brain_enabled
        if not midlong_brain_enabled():
            return None
    except Exception:
        return None
    sym = str(position.get("symbol") or "").upper()
    if not sym or not session_id:
        return None
    try:
        from backend.services.mlto.thesis_store import get as _th_get
        from backend.services.mlto.brain import (
            _inv_price as _th_inv_px,
            thesis_is_tradeable_fresh as _th_tradeable,
        )
        tier = _tier_of(position)
        # [2026-09-07] 三档读论题：short 仓读 short 论题（此前一律读 mid，错配）
        th = _th_get(str(session_id), sym, tier if tier in ("long", "short") else "mid")
        if not _th_tradeable(th):
            return None
        if bool(getattr(th, "should_close", False)):
            return ("thesis_should_close", th)
        inv = getattr(th, "invalidation", None) or {}
        ipx = _th_inv_px(inv)
        mark = float(position.get("mark_price") or position.get("current_price") or 0)
        side = _pos_direction(position.get("side"))
        th_dir = str(getattr(th, "direction", "") or "").lower()
        # 同向才用失效价：多头论题跌破 / 空头论题上破
        if ipx and mark > 0 and th_dir == side and th_dir in ("long", "short"):
            hit = (side == "long" and mark < float(ipx)) or (
                side == "short" and mark > float(ipx)
            )
            if hit:
                # [调研轮18] 与失效条件原文对齐（原文是"收盘跌破"，实现曾是"触碰即平"）：
                # 条件未成立则本轮不触发，保护交回硬止损/追踪/分段止盈（少砍早单）。
                _ok, _why = thesis_invalidation_semantics_ok(
                    position=position, ipx=float(ipx), side=side,
                )
                if _ok:
                    return ("thesis_invalidation", th)
                logger.info("[MidLong] %s %s %s", sym, side, _why)
        # [2026-09-07] 退出传导（周期联动）：本档论题未触发时，若长线论题
        # should_close 且与仓位同向 → 中线/日内同向仓跟随离场（高周期破坏向下传导）。
        if tier in ("mid", "short"):
            try:
                from backend.config.settings import CYCLE_COORDINATOR_ENABLED as _cc_on
            except Exception:
                _cc_on = True
            if _cc_on and tier != "long":
                th_long = _th_get(str(session_id), sym, "long")
                if th_long is not None and _th_tradeable(th_long):
                    lg_dir = str(getattr(th_long, "direction", "") or "").lower()
                    if bool(getattr(th_long, "should_close", False)) and lg_dir and lg_dir == side:
                        return ("thesis_long_propagate", th_long)
    except Exception as exc:
        logger.debug("[MidLong] resolve_thesis_hard_exit 跳过 %s: %s", sym, exc)
    return None


def ack_thesis_hard_exit(thesis, *, reason: str, symbol: str, tier: str, side: str) -> None:
    """平仓成功后复位 should_close 并记事件（幂等失败可忽略）。"""
    if thesis is None:
        return
    try:
        thesis.should_close = False
        from backend.services.mlto import thesis_store as _ts
        _ts._persist(None, thesis)  # noqa: SLF001
        _ts.append_event(
            thesis.thesis_id,
            str(reason or "thesis_should_close"),
            {"symbol": symbol, "tier": tier, "side": side},
        )
    except Exception as exc:
        logger.debug("[MidLong] ack_thesis_hard_exit 跳过: %s", exc)


def thesis_should_close_confirmed(
    db, *, position: Dict[str, Any], thesis, tier: str,
    pnl_pct: float, hold_hours: float,
) -> Tuple[bool, str]:
    """[2026-09-12 F39] flag-only should_close 的反转确认闸。

    数据依据（14 天 163 笔亏损平仓反事实复算）：
    +6h/+24h/+48h 分别 55%/58%/61% 收复平仓价上方——flag-only 判断在年轻小亏仓
    上≈抛硬币（用户实测反馈「小亏后全部离场不合理」）。只有三选一满足才全平：
      1) 价格确认：mark 已突破同向失效价，或论题方向已翻反（真反转判定）；
      2) min_hold 已满（mid 12h / long 72h，M0-11 同契约）且仍浮亏；
      3) 紧急亏损：保证金口径 ≤ -min_hold_emergency_loss_pct（默认 6%）。
    否则返回 (False, 原因)——行情没反转就不离场，flag 保留待后续 tick 复核。
    异常时 fail-open 放行（不改变旧保护语义）。
    """
    try:
        mark = float(position.get("mark_price") or position.get("current_price") or 0)
        side = _pos_direction(position.get("side"))
        th_dir = str(getattr(thesis, "direction", "") or "").lower()
        # 1) 价格确认：同向失效价被突破
        try:
            from backend.services.mlto.brain import _inv_price as _th_inv_px
            inv = getattr(thesis, "invalidation", None) or {}
            ipx = _th_inv_px(inv)
        except Exception:
            ipx = None
        if ipx and mark > 0 and th_dir == side and th_dir in ("long", "short"):
            hit = (side == "long" and mark < float(ipx)) or (
                side == "short" and mark > float(ipx)
            )
            if hit:
                return True, "inv_price_confirmed"
        # 2) 论题方向翻反 = 主脑反转判定
        if th_dir and th_dir in ("long", "short") and th_dir != side:
            return True, "thesis_direction_flipped"
        # 3) min_hold / 紧急豁免（与 M0-11 同契约）
        _mh = _review_min_hold_check(
            db, position=position, pos_tier=tier, pnl_pct=pnl_pct,
            hold_hours=hold_hours, side=side, sym=str(position.get("symbol") or ""),
        )
        if _mh.get("ok"):
            return True, f"min_hold_ok:{_mh.get('detail', '')[:60]}"
        return False, f"wait_price_confirmation:{_mh.get('detail', '')[:60]}"
    except Exception as _e:
        logger.warning("[MidLong] thesis_should_close_confirmed 异常(fail-open 放行): %s", _e)
        return True, "gate_error_fail_open"


def _review_min_hold_check(db, *, position, pos_tier: str, pnl_pct: float,
                           hold_hours: float, side: str, sym: str) -> Dict[str, Any]:
    """[2026-08-22 M0-11] 复查平仓 min_hold 保护。

    规则版/LLM 版方向复查的 "close" 决策，只有满足下述之一才放行：
      - 持仓已超过该 tier 的 min_hold_sec（mid 12h / long 72h，取自
        TIER_PROTECTION_PARAMS，与 master_close_guard 同契约）；
      - 保证金口径亏损 ≥ min_hold_emergency_loss_pct（默认 6%，紧急豁免）；
      - tier 配置缺失/无法读取（fail-open，不改变旧行为）。
    返回 {"ok": bool, "detail": str}。
    """
    try:
        from backend.config.settings import TIER_PROTECTION_PARAMS as _TPP
        tier_norm = (pos_tier or "mid").strip().lower()
        if tier_norm not in ("mid", "long"):
            return {"ok": True, "detail": f"tier={tier_norm} 非 mid/long，放行"}
        _tier_cfg = _TPP.get(tier_norm, _TPP.get("mid", {}))
        _min_hold_sec = float(_tier_cfg.get("min_hold_sec") or 0)
        if _min_hold_sec <= 0:
            return {"ok": True, "detail": f"tier={tier_norm} min_hold_sec 未配置，放行"}
        if hold_hours * 3600.0 >= _min_hold_sec:
            return {"ok": True, "detail": f"held {hold_hours:.1f}h ≥ min_hold {_min_hold_sec/3600:.1f}h"}
        # 紧急亏损豁免：margin 口径亏损超过阈值（与 master_close_guard 同口径：
        # min_hold_emergency_loss_pct = 保证金口径 6%）
        _emerg_pct = float(_tier_cfg.get("min_hold_emergency_loss_pct") or 6) / 100.0
        _margin_pct = abs(pnl_pct)
        if _margin_pct >= _emerg_pct:
            return {"ok": True, "detail": f"emergency loss {_margin_pct:.1%}≥{_emerg_pct:.1%}，豁免"}
        return {
            "ok": False,
            "detail": (f"{side} {sym} held {hold_hours:.1f}h < min_hold "
                       f"{_min_hold_sec/3600:.1f}h，仓位兑现窗口未到（M0-11）"),
        }
    except Exception as _e:
        # [§59 修复] 该检查是「最短持有期」软闸，异常放行=闸未生效，必须可见
        logger.warning("[MidLong] stage=manage %s review_min_hold 检查异常(fail-open，放行): %s", sym, _e)
        return {"ok": True, "detail": f"检查异常放行: {_e}"}


def trend_broken_price_gate(
    position: Dict[str, Any], *, side: str, tier: str,
) -> Tuple[bool, str]:
    """[2026-09-11 深度解析 V11] trend_broken（方向复查平仓）的价格闸。

    ## 数据依据（本机复算，`_audit_ml/V11_discretionary_exit_value.py`）

    long 组 114 笔入场：
      * 实际出场（含 trend_broken 裁量砍仓）：均 **−0.58 USD/笔**，合计 −66.37；
      * 同一批入场套用 ExitPolicy（SL6% / 追踪 3-1.5% / 168h）反事实：均 **+2.454%**
        （胜率 65.8%）。
    其中 `trend_broken` 通道 24 笔：实际均 −4.10（合计 −98.46），政策反事实 **+8.127%**
    （胜率 83.3%，出场分布全是 trail）——即 4h 级"趋势破坏"多在噪音区内触发，
    把仓位砍在 72h 兑现窗口之前（与《中长线负期望根因报告》§2「边际需 72h+」一致）。
    同时段 `thesis_*`（12 笔）与 `breakeven_tp`/`long_trend_v2`（35 笔）的裁量出场
    **优于**政策反事实 → 本闸只针对 trend_broken，不动其它通道。

    ## 闸语义

    价格口径浮亏 < `MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS`（默认 3.0%，即 SL6% 的一半）
    且**日线 regime 非 down** 时：不执行平仓（返回 False），把亏损交给 SL/追踪决定；
    浮亏已 ≥ 阈值（SL 也快到了）或日线转下行（down-regime 做多是负边际，实测 t=−4.09）
    时仍允许平仓。阈值置 0 = 关闭本闸（回滚）。数据缺失 fail-open（放行，保持旧行为）。
    """
    try:
        gate = float(_cfg_float("MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS", 3.0) or 0.0)
    except Exception:
        gate = 3.0
    if gate <= 0:
        return True, "价格闸关闭"
    t = str(tier or "").strip().lower()
    if t not in ("mid", "long"):
        return True, f"tier={t} 不适用"
    try:
        sym = str(position.get("symbol") or "").upper()
        entry = float(position.get("entry_price") or 0)
        mark = float(
            position.get("mark_price") or position.get("current_price") or 0
        )
        if entry <= 0 or mark <= 0:
            return True, "无价格数据(fail-open)"
        s = str(side or "").strip().lower()
        loss_pct = (
            (entry - mark) / entry * 100.0 if s in ("long", "buy")
            else (mark - entry) / entry * 100.0
        )
        if loss_pct >= gate:
            return True, f"浮亏 {loss_pct:.2f}% ≥ {gate:.1f}%（SL 同向，放行）"
        # 日线下行 regime：裁量平仓仍放行（down-regime 做多为负边际）
        try:
            from backend.services.full_auto.midlong_circuit_gate import _daily_regime
            if _daily_regime(sym) == "down":
                return True, "日线 regime=down（放行）"
        except Exception:
            pass
        return False, f"浮亏仅 {loss_pct:.2f}% < {gate:.1f}%（噪音区不砍仓，交给 SL/追踪）"
    except Exception as exc:  # noqa: BLE001 — 闸自身异常必须放行
        return True, f"价格闸异常放行: {exc}"


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


def clamp_tighten_band(
    *, side: str, mark: float, new_sl: float, tier: str,
) -> "tuple[float, str]":
    """[2026-09-18 轮96 修 Fix B] 车道**最小收紧带宽**：把过近的追踪止损放宽。

    返回 `(可能被放宽后的 new_sl, 说明文字或空串)`。

    为什么需要：`tighten_trailing` 的带宽是
        `mark × market_summary[sym].volatility_value × trailing_atr_mult`
    而 `volatility_value` 是**短周期**波动率（ETH/LINK 实测 ≈0.5%），
    `trailing_atr_mult` 默认 2.0 ⇒ 带宽 ≈ **1% 价格**。
    对"最短持仓 12h、设计持仓 3–7 天"的车道，1% 回撤是噪音不是趋势反转：
    2026-09-18 实测 4 笔 `trend_follow`（+3.0%~+6.0% 峰值，持仓仅 9.4–13.6h）
    与 3 笔 mid 全部在止损线附近被收割，`close_reason` 清一色 `breakeven_tp`
    （见 `reports/_轮96_长线趋势仓被收紧止损收割事故复盘_20260918.md`）。

    下限取 `MIDLONG_TIGHTEN_MIN_BAND_PCT_<TIER>`（在 `settings.py` 声明，
    经 `_cfg_float` 读取 —— settings 优先、env 兜底）；未配置时按车道默认
    （long 3%、mid 1%、short 0=不设限）。方向语义：
      · 只**放宽**本次提议（多头把止损往下放、空头往上放），
        **绝不主动下调已有的更高止损** —— 是否接受这个更宽的值由
        `paper_engine.update_position_tp_sl` 的"只收紧不放宽"规则裁决，
        所以本函数不可能把保护撤掉。
      · 无法解析的 tier → 不设限（保持原行为），避免因配置缺失改变语义。
    """
    try:
        _tier = str(tier or "").strip().lower()
        _pct = _cfg_float(f"MIDLONG_TIGHTEN_MIN_BAND_PCT_{_tier.upper()}", 0.0)
        if _pct <= 0:
            _pct = {"long": 0.03, "mid": 0.01}.get(_tier, 0.0)
        _mark = float(mark or 0)
        _sl = float(new_sl or 0)
        if _pct <= 0 or _mark <= 0 or _sl <= 0:
            return _sl, ""
        _is_long = str(side or "").lower() in ("long", "buy")
        _floor_sl = _mark * (1 - _pct) if _is_long else _mark * (1 + _pct)
        _too_close = (_sl > _floor_sl) if _is_long else (_sl < _floor_sl)
        if not _too_close:
            return _sl, ""
        return _floor_sl, (
            f"tier={_tier} tighten 带宽不足保护: 提议 SL={_sl:.6f} "
            f"距现价 {abs(_mark - _sl) / _mark * 100:.3f}% < 下限 {_pct * 100:.2f}% "
            f"→ 放宽到 {_floor_sl:.6f}（Fix B 车道最小带宽）"
        )
    except Exception as _e:  # pragma: no cover - 纯计算，不应发生
        logger.debug("[MidLong] clamp_tighten_band 异常(按原值): %s", _e)
        return float(new_sl or 0), ""


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
        # [P0-10 双重减仓收口] v2 统一分段止盈开启时本路径必须短路：
        # paper 引擎 _run_unified_staged_tp（全层级，30s/tick）与 _dim_staged_tp（long 层）
        # 并行会对同一 long 仓各自减仓。v2 是唯一权威，此处 hold。
        from backend.config.settings import RISK_V2_UNIFIED_STAGED_TP
        if RISK_V2_UNIFIED_STAGED_TP:
            return {"action": "hold", "channel": "", "reason": "v2_unified_staged_tp_on"}
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


def _time_stop_decision(hold_hours: float, pnl_pct: float, *,
                        full_h: float = 24.0, reduce_h: float = 8.0) -> Tuple[str, str]:
    """[P3 大轮回 2026-09-27] §6.1 时间止损纯决策：24h 全平 / 8h 未达 μ(pnl≤0) 减半。

    阈值 0 = 关闭该档；full 优先于 reduce。返回 (action, reason)：
    ("close","time_stop_full") / ("reduce","time_stop_reduce") / ("hold","")。
    """
    if full_h > 0 and hold_hours >= full_h:
        return "close", "time_stop_full"
    if reduce_h > 0 and hold_hours >= reduce_h and pnl_pct <= 0:
        return "reduce", "time_stop_reduce"
    return "hold", ""


def _channel_shadowed(reason: Any, tier: str) -> bool:
    """出场通道熔断（阶段2，风控诊断 B3）：close_reason×tier 近 30 笔 wr<40% → shadow。

    历史 0% 通道（trend_review_close 12 笔 -44.90、master_running_close 148 笔 -35.42）
    属于"系统自己的砍仓通道"——shadow 后只记录不执行，防继续出血。
    """
    try:
        from backend.services.source_attribution import attribution as _attr2
        return bool(_attr2.exit_channel_shadow(str(reason or ""), tier or ""))
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────
# 维度 ① + ③：方向延续性复查 + TP/SL 调整（LLM，节流）
# ──────────────────────────────────────────────────────────────────────
def _midlong_review_use_llm() -> bool:
    """[2026-08-16 用户指令] 热路径去 LLM：默认走确定性规则复查。

    MIDLONG_REVIEW_LLM=true 可回滚到 trend_agent LLM 复查。
    """
    try:
        return os.getenv("MIDLONG_REVIEW_LLM", "false").strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        return False


def _tf_vote(bias: Any, macd: Any, trend: Any) -> str:
    """单一周期方向投票：bullish / bearish / mixed（多信号多数决）。"""
    votes = 0
    b = str(bias or "").strip().lower()
    if b == "bullish":
        votes += 1
    elif b == "bearish":
        votes -= 1
    try:
        m = float(macd or 0)
        if m > 0:
            votes += 1
        elif m < 0:
            votes -= 1
    except Exception:
        pass
    t = str(trend or "").strip().lower()
    if t in ("bullish", "up", "多头", "上升"):
        votes += 1
    elif t in ("bearish", "down", "空头", "下降"):
        votes -= 1
    if votes > 0:
        return "bullish"
    if votes < 0:
        return "bearish"
    return "mixed"


def _rule_direction(symbol: str, position: Dict[str, Any],
                    market_summary: Dict[str, Any]) -> Dict[str, Any]:
    """规则版方向复查：确定性多周期共振（替代 trend_agent LLM）。

    与 LLM 版吃同一份数据（orchestrator bias + indicators 4h/1d 的
    macd/trend）。只在 4h 与 1d **同时反向** 时才判 close（趋势破坏）；
    单周期反向仅 hold（避免噪声误杀）。
    """
    side = _pos_direction(position.get("side")) or "long"
    pos_dir = 1 if side == "long" else -1
    md = (market_summary or {}).get(str(symbol).upper()) or (market_summary or {}).get(str(symbol)) or {}
    if not isinstance(md, dict):
        md = {}
    i4 = md.get("indicators_4h") if isinstance(md.get("indicators_4h"), dict) else {}
    i1 = md.get("indicators_1d") if isinstance(md.get("indicators_1d"), dict) else {}
    orch = md.get("orchestrator") if isinstance(md.get("orchestrator"), dict) else {}
    tf4 = _tf_vote(orch.get("mid_bias"), i4.get("macd"), i4.get("trend"))
    tf1 = _tf_vote(orch.get("long_bias") or orch.get("mid_bias"), i1.get("macd"), i1.get("trend"))
    opp4 = (tf4 == "bearish" and pos_dir > 0) or (tf4 == "bullish" and pos_dir < 0)
    opp1 = (tf1 == "bearish" and pos_dir > 0) or (tf1 == "bullish" and pos_dir < 0)
    if opp4 and opp1:
        return {
            "action": "close",
            "reasoning": f"多周期共振反向：4h={tf4} 1d={tf1} 与{side}相悖（规则复查）",
        }
    if opp4 or opp1:
        return {
            "action": "hold",
            "reasoning": f"单周期反向（4h={tf4} 1d={tf1}），继续持有观察",
        }
    return {"action": "hold", "reasoning": f"多周期趋势支持（4h={tf4} 1d={tf1}）"}


def _four_h_vote(symbol: str, market_summary: Dict[str, Any]) -> str:
    """4h 单周期方向投票（bullish/bearish/mixed）。

    [2026-09-16 验收轮6] 供 ⑥b 4h 反转离场使用：与 _rule_direction 同口径，
    但只看 4h——中线仓不应等 1d 也反转才走（两日 giveback ~303 的教训：
    4h 已反转、1d 未反转的窗口里仓位从峰值回撤殆尽并打到 SL）。
    """
    md = (market_summary or {}).get(str(symbol).upper()) or (market_summary or {}).get(str(symbol)) or {}
    if not isinstance(md, dict):
        md = {}
    i4 = md.get("indicators_4h") if isinstance(md.get("indicators_4h"), dict) else {}
    orch = md.get("orchestrator") if isinstance(md.get("orchestrator"), dict) else {}
    return _tf_vote(orch.get("mid_bias"), i4.get("macd"), i4.get("trend"))


def _mid_4h_reversal_reason(
    symbol: str, position: Dict[str, Any], market_summary: Dict[str, Any],
    *, pos_tier: str, side: str, hold_hours: float, pnl_pct: float,
) -> Optional[str]:
    """⑥b 判定（纯函数）：mid 仓 4h 单周期反转且论点未兑现 → 返回离场理由，否则 None。

    [2026-09-16 验收轮6] 条件：开关开 + tier=mid + 持有≥2h + 4h 投票反向
    + 浮盈≤+0.3%（保证金口径）。factor_route 仓同样适用（不再跳过）。
    """
    if not _cfg_bool("MIDLONG_MID_4H_REVERSAL_EXIT", True):
        return None
    if pos_tier != "mid" or hold_hours < 2.0:
        return None
    _tf4 = _four_h_vote(symbol, market_summary)
    _opp4 = (_tf4 == "bearish" and side == "long") or (_tf4 == "bullish" and side == "short")
    if not _opp4 or pnl_pct > 0.003:
        return None
    return f"4h={_tf4} 与{side}相悖 持有{hold_hours:.1f}h 浮盈{pnl_pct*100:+.2f}% 论点未兑现"


def _dim_direction(
    db, *, account_id, symbol: str, position: Dict[str, Any],
    market_summary: Dict[str, Any], analyst_reports: Dict[str, Any],
) -> Dict[str, Any]:
    """六维中的①③（方向延续性）。默认规则版；MIDLONG_REVIEW_LLM=true 走 LLM。"""
    if not _midlong_review_use_llm():
        return _rule_direction(symbol, position, market_summary)
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
    """判断是否滚仓。返回 {"action": "add"/"wait"/"skip", "ratio", "reasoning"}。

    [2026-08-16] 默认规则版：浮盈 + 多周期同向 才建议加仓（5 层门控仍会复核）；
    MIDLONG_REVIEW_LLM=true 走 trend_agent LLM。
    """
    if not _midlong_review_use_llm():
        side = _pos_direction(position.get("side")) or "long"
        pos_dir = 1 if side == "long" else -1
        md = (market_summary or {}).get(str(symbol).upper()) or (market_summary or {}).get(str(symbol)) or {}
        if not isinstance(md, dict):
            md = {}
        i4 = md.get("indicators_4h") if isinstance(md.get("indicators_4h"), dict) else {}
        i1 = md.get("indicators_1d") if isinstance(md.get("indicators_1d"), dict) else {}
        orch = md.get("orchestrator") if isinstance(md.get("orchestrator"), dict) else {}
        tf4 = _tf_vote(orch.get("mid_bias"), i4.get("macd"), i4.get("trend"))
        tf1 = _tf_vote(orch.get("long_bias") or orch.get("mid_bias"), i1.get("macd"), i1.get("trend"))
        pnl_pct = _pnl_pct_of(position)
        _d4 = 1 if tf4 == "bullish" else (-1 if tf4 == "bearish" else 0)
        _d1 = 1 if tf1 == "bullish" else (-1 if tf1 == "bearish" else 0)
        if pnl_pct >= 0.03 and _d4 == pos_dir and _d1 == pos_dir:
            return {
                "action": "add", "ratio": 0.25,
                "reasoning": f"规则滚仓：浮盈{pnl_pct:.1%}且4h/1d同向(4h={tf4},1d={tf1})",
            }
        return {
            "action": "skip",
            "reasoning": f"规则滚仓跳过：浮盈{pnl_pct:.1%} 4h={tf4} 1d={tf1}",
        }
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
    # [2026-08-22 M0-6] 跨层防护：持仓管理只允许平 mid/long 性质仓（swing/trend_follow/
    # position），禁止平 scalp 仓；传 position_id 精确定位，杜绝"平错腿"。
    _nature = str(position.get("trade_nature") or "").lower()
    _tier = str(position.get("timeframe_tier") or "").lower()
    if _nature not in ("swing", "trend_follow", "position") and _tier not in ("mid", "long"):
        logger.warning(
            "[MidLong] stage=manage %s[%s] nature=%s tier=%s 非中长线仓，拒绝平仓 (M0-6 跨层防护)",
            sym, side, _nature or "?", _tier or "?",
        )
        return None
    # [§78 执行 2026-09-11 / 决策 P19-B] **通道熔断（MLTO 路径）**：
    # 这里是 mid/long 唯一平仓收口点（review / 论题哨兵 / 兜底共 5 个调用点）。
    # 判据在共享闸 `services/exit/channel_breaker_gate.py`：
    #   保护性通道（sl/tp/强平/紧急/硬事实/浮盈保护/超时/尘仓…）**永不抑制**；
    #   仅"叙事/系统裁量"通道参与熔断；`EXIT_CHANNEL_BREAKER_UNIFIED=false` 可一键回滚。
    try:
        from backend.services.exit.channel_breaker_gate import should_suppress as _cb_gate
        _sup, _why = _cb_gate(str(reason or ""), _tier)
        if _sup:
            logger.warning(
                "[MidLong] stage=manage symbol=%s 离场被**通道熔断**抑制 reason=%s（命中 %s）",
                sym, str(reason or "")[:60], _why,
            )
            try:
                host.append_event(
                    session, "exit_channel_broken",
                    f"🚫 [通道熔断] {sym}[{side}] 抑制离场 {str(reason or '')[:60]}（命中 {_why}）",
                )
            except Exception:
                pass
            return None
    except Exception as _cb_err:  # fail-open 但可见
        logger.warning("[MidLong] stage=manage %s 通道熔断检查异常(fail-open): %s", sym, _cb_err)
    try:
        from backend.services.paper_trading_engine import paper_engine
        res = paper_engine.close_position(
            db, account_id, sym, side, reason=str(reason)[:120],
            strategy_id=position.get("strategy_id"),
            position_id=position.get("id"),
            trade_nature=_nature or None,
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
    # [2026-08-22 M0-6] 跨层防护：只减 mid/long 仓（同 _exec_close）
    _nature = str(position.get("trade_nature") or "").lower()
    _tier = str(position.get("timeframe_tier") or "").lower()
    if _nature not in ("swing", "trend_follow", "position") and _tier not in ("mid", "long"):
        logger.warning(
            "[MidLong] stage=manage %s[%s] nature=%s tier=%s 非中长线仓，拒绝减仓 (M0-6 跨层防护)",
            sym, side, _nature or "?", _tier or "?",
        )
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
            position_id=position.get("id"),
            trade_nature=_nature or None,
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
            # [轮96 Fix C] 标注来源：这是**追踪派生**止损（随行情贴近市价），
            # 不是结构位。paper 引擎会在该仓位的 min_hold 保护期内拒付它。
            sl_source="trailing",
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


def _pyramid_rule_prethrottle_ready(
    symbol: str, position: Dict[str, Any], market_summary: Dict[str, Any],
) -> bool:
    """[2026-09-28] 节流前的规则直通判据（纯函数，供单测锁定）。

    条件 = 保证金口径浮盈 > `MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL`(默认5%)
           且 4h 与 1d 双周期都与持仓方向同向（与 `_dim_pyramid` 规则分支同口径）。
    只回答"该不该进 5 层门控"，不执行任何动作。
    """
    sym_u = str(symbol or "").upper()
    margin = float(position.get("margin", 0) or 0)
    upnl = float(position.get("unrealized_pnl", 0) or 0)
    margin_pnl = (upnl / margin) if margin > 0 else 0.0
    if margin_pnl <= _cfg_float("MIDLONG_POSITION_MGMT_PYRAMID_DIRECT_PNL", 0.05):
        return False
    md = (market_summary or {}).get(sym_u) or (market_summary or {}).get(sym_u.upper()) or {}
    md = md if isinstance(md, dict) else {}
    i4 = md.get("indicators_4h") if isinstance(md.get("indicators_4h"), dict) else {}
    i1 = md.get("indicators_1d") if isinstance(md.get("indicators_1d"), dict) else {}
    orch = md.get("orchestrator") if isinstance(md.get("orchestrator"), dict) else {}
    tf4 = _tf_vote(orch.get("mid_bias"), i4.get("macd"), i4.get("trend"))
    tf1 = _tf_vote(orch.get("long_bias") or orch.get("mid_bias"), i1.get("macd"), i1.get("trend"))
    pos_dir = 1 if (_pos_direction(position.get("side")) or "long") == "long" else -1
    align4 = (tf4 == "bullish" and pos_dir > 0) or (tf4 == "bearish" and pos_dir < 0)
    align1 = (tf1 == "bullish" and pos_dir > 0) or (tf1 == "bearish" and pos_dir < 0)
    return align4 and align1


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


def _exec_controlled_dca(
    db, *, account_id, position: Dict[str, Any], host, session,
) -> bool:
    """[2026-09-12 F40] 行情未反转时的受控逆势补仓执行。

    全部门控在 position_manager.evaluate_dca 内部（最多补 1 次 / 亏损带 -2%~-8% /
    2h 冷却 / 同向敞口≤权益50% / 资金充足 / SL 地板不得比原仓更差），
    补仓杠杆减半（防死亡螺旋）。调用方保证「论题同向有效 + 反转价格闸未触发」。
    """
    sym = str(position.get("symbol", "") or "").upper()
    side = _pos_direction(position.get("side"))
    if not sym or not side:
        return False
    tier = _tier_of(position)
    try:
        from backend.services.paper_trading_engine import paper_engine
        from backend.services.position_memory_manager import position_manager

        plan = position_manager.evaluate_dca(
            db=db, account_id=account_id, symbol=sym, side=side,
            ai_confidence=0.60,  # 论题同向有效即 0.60 > DCA_MIN_CONFIDENCE=0.40
            current_price=float(position.get("mark_price", 0) or 0),
            existing_position=position,
            volatility_pct=0.015,
            market_regime="unknown",
            orchestrator_decision=None,  # 方向支持由论题同向前置闸保证
            risk_score=50.0,
            tier=tier,
        )
        if plan.action != "dca":
            host.append_event(
                session, "pos_mgmt_dca_skip",
                f"📊 [持仓管理] {sym} 补仓门控拦截: {getattr(plan, 'reasoning', '') or 'no_reason'}",
            )
            return False

        mark = float(position.get("mark_price", 0) or 0)
        qty = plan.notional_usd / mark if mark and mark > 0 else 0
        if qty <= 0:
            return False
        # 保守杠杆：补仓杠杆减半（与 master_execution DCA 同款防死亡螺旋）
        _dca_lev = min(5.0, max(2.0, float(position.get("leverage", 10) or 10) / 2.0))
        result = paper_engine.place_order(
            db, account_id, sym,
            "buy" if side == "long" else "sell",
            quantity=qty, leverage=_dca_lev,
            tp_price=plan.take_profit_price, sl_price=plan.stop_loss_price,
            strategy_id=position.get("strategy_id"),
            timeframe_tier=tier,
            trade_nature=position.get("trade_nature"),
            add_type="dca",
        )
        if result and result.get("status") == "filled":
            host.append_event(
                session, "pos_mgmt_dca",
                f"📉 [持仓管理] 逆势补仓 {sym}[{side}] +${plan.margin_usd:.0f} | {getattr(plan, 'reasoning', '') or ''}",
            )
            logger.info(
                "[MidLong] stage=manage symbol=%s action=dca margin=%.0f qty=%s",
                sym, plan.margin_usd or 0, qty,
            )
            return True
        logger.info("[MidLong] stage=manage %s 补仓下单未成交", sym)
    except Exception as e:
        logger.warning("[MidLong] stage=manage %s 补仓执行异常: %s", sym, e)
    return False


def _dim_controlled_dca(
    db, *, account_id, position: Dict[str, Any], host, session,
) -> Dict[str, Any]:
    """[2026-09-12 F40] 六维④：行情未反转时的受控逆势补仓前置闸。

    前置条件（全满足才尝试补仓；任一不满足 → skip，绝不平仓）：
      1) 论题有效、方向与仓位同向、无待确认的 should_close（与 F39 同口径）；
      2) 反转价格闸（trend_broken_price_gate）判定「噪音区未破位」；
      3) 同向失效价未被突破。
    """
    out: Dict[str, Any] = {"action": "skip", "channel": None, "reasoning": ""}
    sym = str(position.get("symbol", "") or "").upper()
    side = _pos_direction(position.get("side"))
    tier = _tier_of(position)
    if not sym or not side:
        return out
    try:
        _sid = str(getattr(session, "session_id", "") or "")
        from backend.services.mlto.thesis_store import get as _th_get
        from backend.services.mlto.brain import (
            _inv_price as _th_inv_px,
            thesis_is_tradeable_fresh as _th_fresh,
        )
        th = _th_get(_sid, sym, tier if tier in ("long", "short") else "mid")
        if th is None or not _th_fresh(th):
            _dca_precheck_note(sym, "no_thesis", "无论题/论题过期")
            return {**out, "channel": "no_thesis", "reasoning": "无论题/论题过期"}
        th_dir = str(getattr(th, "direction", "") or "").lower()
        if th_dir != side:
            _dca_precheck_note(sym, "thesis_dir_mismatch", f"论题方向 {th_dir} ≠ 仓位 {side}")
            return {**out, "channel": "thesis_dir_mismatch",
                    "reasoning": f"论题方向 {th_dir} ≠ 仓位 {side}"}
        if bool(getattr(th, "should_close", False)):
            _dca_precheck_note(sym, "should_close_pending", "should_close 待确认：不平也不补")
            return {**out, "channel": "should_close_pending",
                    "reasoning": "should_close 待确认：不平也不补"}
        # 反转价格闸：True=放行平仓（亏损深/下行 regime）→ 禁止补仓；
        # False=噪音区（行情未反转）→ 补仓候选
        _tb_ok, _tb_why = trend_broken_price_gate(position, side=side, tier=tier)
        if _tb_ok:
            _dca_precheck_note(sym, "trend_broken_gate", f"反转价格闸放行平仓侧({_tb_why})，禁止补仓")
            return {**out, "channel": "trend_broken_gate",
                    "reasoning": f"反转价格闸放行平仓侧({_tb_why})，禁止补仓"}
        # 同向失效价未破
        inv = getattr(th, "invalidation", None) or {}
        ipx = _th_inv_px(inv)
        mark = float(position.get("mark_price", 0) or 0)
        if ipx and mark > 0 and th_dir == side:
            hit = (side == "long" and mark < float(ipx)) or (
                side == "short" and mark > float(ipx)
            )
            if hit:
                _dca_precheck_note(sym, "inv_breached", f"失效价 {ipx} 已破，禁止补仓")
                return {**out, "channel": "inv_breached",
                        "reasoning": f"失效价 {ipx} 已破，禁止补仓"}
    except Exception as e:
        logger.debug("[MidLong] F40 前置检查异常(skip): %s", e)
        _dca_precheck_note(sym, "precheck_error", str(e)[:80])
        return {**out, "channel": "precheck_error", "reasoning": str(e)[:80]}
    if _exec_controlled_dca(db, account_id=account_id, position=position,
                            host=host, session=session):
        return {"action": "dca_executed", "channel": "dca",
                "reasoning": "逆势补仓已成交（论题有效+噪音区）"}
    return {**out, "channel": "dca_gate_skip", "reasoning": "补仓门控未通过（冷却/带外/敞口）"}


_dca_precheck_logged: Dict[str, float] = {}


def _dca_precheck_note(sym: str, channel: str, reasoning: str) -> None:
    """F40 前置 skip 的可观测限流日志（每 (symbol,channel) 最多 1 条/小时）。"""
    now = time.time()
    key = f"{sym}:{channel}"
    if now - _dca_precheck_logged.get(key, 0.0) < 3600.0:
        return
    _dca_precheck_logged[key] = now
    logger.info("[MidLong] F40 补仓前置 skip %s channel=%s: %s", sym, channel, reasoning)


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

    # ── [2026-09-18 轮96 修 Fix A / 轮99 收窄] 趋势车道只允许「规则失效」与「滚仓」──
    # `trend_e1_engine` 的模块文档（`:5-23`）声明：
    #     出场 = 规则失效 或 收盘 < Chandelier(最高收盘 − 3×ATR20)
    #     `long_trend_v2.manage_long_position（midlong 循环）跳过`，不再双重管理
    #     `paper 引擎 max_hold 复审跳过`；**唯一出场 = 规则失效 / Chandelier**
    # 但实现侧只在三处设了守卫（只管开仓 / 只管 max_hold / 只管 PEO），
    # **本模块全文没有一处 `is_e1_position`** ⇒ "midlong 循环跳过 E1"只是文档承诺。
    #
    # 后果（2026-09-18 实测，见 reports/_轮96_..._事故复盘.md）：
    # 趋势复查给出 `tighten_trailing` 时按 `SL = 现价 − 2×volatility_value`（≈1%）
    # 把止损拉到贴近市价 ⇒ 下一轮 1% 级回撤即把设计持仓 3–7 天的趋势仓按 +3%~+6% 全平。
    #
    # ⚠️ 轮99 收窄：轮96 的第一版是"整个管理器跳过 E1"，**过宽** ——
    # 它连**滚仓(pyramid)**一起停掉了，而用户明确要求"长线趋势是滚仓盈利为目的的"
    # （实测近 30 天长线平均加仓 0.06 次 ≈ 从不加仓）。
    # 现在的语义按车道契约精确划分：
    #     ✅ 允许：论题规则失效退出（= "规则失效"，下方 thesis 块）
    #     ✅ 允许：滚仓/加仓（pyramid 门控链）
    #     ⛔ 禁止：`tighten_trailing` 收紧追踪止损（把趋势仓变成日内仓的元凶）
    #     ⛔ 禁止：`reduce` 裁量减仓（趋势仓应"失效即全平"或跟随 Chandelier，不做千刀万剐）
    # 回滚：`EXIT_TREND_LANE_SKIP_INTRADAY=false`（与引擎侧同一开关，语义一致）。
    _trend_lane_pos = False
    try:
        from backend.services.trend_e1_engine import is_e1_position as _is_e1_pos
        _trend_lane_pos = bool(_is_e1_pos(position))
    except Exception as _e1_err:
        logger.warning(
            "[MidLong] E1 独占判定不可用（按非 E1 继续，存在误收紧 E1 趋势仓的风险）: %s",
            _e1_err,
        )
    if not _trend_lane_pos:
        # 非 E1 但仍是长线车道（tier=long / nature=trend_follow|position）同样适用。
        # [轮100] 归属判定统一走车道策略真源（`config/lane_policy.py`），
        # 不再在各模块各写一份 (tier, nature) 元组 —— 那正是"中长线合并"的病根。
        try:
            from backend.config.lane_policy import is_long_lane as _is_long_lane
            _trend_lane_pos = bool(_is_long_lane(
                tier=position.get("timeframe_tier"),
                nature=position.get("trade_nature"),
            ))
        except Exception:
            _tier_p = str(position.get("timeframe_tier") or "").strip().lower()
            _nat_p = str(position.get("trade_nature") or "").strip().lower()
            _trend_lane_pos = _tier_p == "long" or _nat_p in ("trend_follow", "position")
    if _trend_lane_pos:
        try:
            from backend.config import settings as _st99
            if not bool(getattr(_st99, "EXIT_TREND_LANE_SKIP_INTRADAY", True)):
                _trend_lane_pos = False
        except Exception:
            pass

    # [2026-09-05] LLM 主脑：论题 should_close / 失效价优先于叙事复查。
    # 硬止损、组合超限、吊灯减仓仍走下方规则，不等 LLM。
    # 开平同权：触发后写事件并复位 should_close，防重复平仓。
    # [2026-09-07] 判定抽到 resolve_thesis_hard_exit（与 active_exit 哨兵同口径）。
    # [轮102 阶段3] 长线车道"允许做什么"的判定归属已抽到
    # `full_auto/trend_lane_manager.py`（纯函数）：本处只用它的结论，
    # 不再把「长线禁止 tighten/reduce、允许规则失效与滚仓」散在 2000 行里。
    try:
        _sid = str(getattr(session, "session_id", "") or "")
        _hit = resolve_thesis_hard_exit(_sid, position)
        if _hit:
            _reason, _th = _hit
            _lane_dec = None
            if _trend_lane_pos:
                try:
                    from backend.services.full_auto.trend_lane_manager import decide as _trend_decide
                    _lane_dec = _trend_decide(thesis_reason=str(_reason or ""))
                except Exception as _tl_err:
                    logger.debug("[MidLong] 车道决策模块不可用(按旧路径继续): %s", _tl_err)
            # [轮102] 长线车道：只有**规则失效**允许直接全平；LLM 裁量（should_close）
            # 一律走下方 F39 闸，且裁量平仓在长线属于"需过闸"的动作
            # （`_lane_dec.detail["requires_confirm_gate"]`）。
            if _lane_dec is not None and not _lane_dec.allowed:
                logger.info("[MidLong] %s tier=%s 车道决策=%s（%s）→ 不走论题平仓",
                            sym, position.get("timeframe_tier"),
                            _lane_dec.action, _lane_dec.reason)
                _hit = None
        if _hit:
            _reason, _th = _hit
            # [2026-09-12 F39] flag-only should_close 反转确认闸：行情未反转（无价格
            # 确认/未满 min_hold/非紧急亏损）→ 不平仓。反事实：14 天 163 笔亏损平仓
            # 55-61% 在 6-48h 内收复平仓价——flag-only 判断≈抛硬币（用户实测反馈）。
            if _reason == "thesis_should_close" and _cfg_bool(
                "MIDLONG_THESIS_CLOSE_CONFIRM_ENABLED", True
            ):
                _f39_pnl = _pnl_pct_of(position)
                _f39_hold = _held_hours(position, db)
                _f39_ok, _f39_why = thesis_should_close_confirmed(
                    db, position=position, thesis=_th, tier=_tier_of(position),
                    pnl_pct=_f39_pnl, hold_hours=_f39_hold,
                )
                if not _f39_ok:
                    # 价格已收复（盈利）→ should_close 被行情证伪：撤销 flag 继续持有
                    if _f39_pnl >= 0:
                        try:
                            _th.should_close = False
                            from backend.services.mlto import thesis_store as _ts2
                            _ts2._persist(None, _th)
                            _ts2.append_event(
                                _th.thesis_id, "should_close_recovered", {"symbol": sym},
                            )
                        except Exception:
                            pass
                        _out["hold_reason"] = "thesis_should_close_recovered"
                        _out["reasoning"] = "should_close 被行情收复证伪，撤销并继续持有"
                        return _out
                    logger.info(
                        "[MidLong] stage=manage %s should_close 被反转确认闸拦截(%s)——行情未反转不平仓",
                        sym, _f39_why,
                    )
                    host.append_event(
                        session, "thesis_close_blocked",
                        f"[F39] {sym} should_close 等待价格确认: {_f39_why}",
                    )
                    _out["hold_reason"] = "thesis_should_close_wait_confirmation"
                    _out["reasoning"] = f"should_close 等待价格确认（{_f39_why}）"
                    return _out
            _closed = _exec_close(
                db, account_id=account_id, position=position,
                reason=_reason, host=host, session=session,
            )
            if _closed:
                ack_thesis_hard_exit(
                    _th, reason=_reason, symbol=sym,
                    tier=_tier_of(position), side=position.get("side"),
                )
            return {
                "action": "manage_close", "score": 0, "direction": "manage",
                "reasoning": f"论题 {_reason}", "hold_reason": _reason,
            }
    except Exception as _thc_err:
        logger.warning("[MidLong] stage=manage %s 论题离场检查跳过(fail-open，软退出未评估): %s", sym, _thc_err)

    # [2026-08-16 long_trend_v2] 长线仓改由 V2 每日管理器接管（Chandelier/结构退出/
    # 新高金字塔），跳过本模块的短中线口径（分档TP/保本/15min复查/bias反转）。
    try:
        from backend.services.long_trend_v2 import long_v2_enabled
        _v2 = long_v2_enabled()
    except Exception:
        _v2 = False
    if _v2 and _tier_of(position) == "long":
        _out["direction"] = "long_trend_v2"
        _out["hold_reason"] = "long_trend_v2_daily_managed"
        _out["reasoning"] = "长线仓由 long_trend_v2 每日管理器接管"
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

    # [M1-B 2026-08-21] 入场来源（出场分流）：提前加载 exit_state_json，
    # factor_route 仓禁方向复查/叙事平仓（防碎平），仅 SL/TP/时间/因子失效离场。
    # 历史仓无键 → unknown，行为与现码一致。此处加载的 _db_pos/_state 供
    # 下方 LLM 节流块复用，不再重复查库。
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
        except Exception as _es_err:
            logger.debug("[MidLong] stage=manage %s 读取 exit_state 失败: %s", sym, _es_err)
    _entry_source = str(_state.get("entry_source") or "unknown").strip().lower()
    _is_factor_pos = _entry_source == "factor_route"

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

    # ═══ 长线车道：独占动作流水线（轮103 阶段3b）═══
    # 轮99 只拦住了 tighten_trailing / reduce 两条，而本函数实际有 7 条动作路径；
    # 长线仓此前照样在跑另 5 条（叙事反转 / 4h反转 / 分批止盈 / 逆势补仓 / 方向破坏离场）
    # —— 那就是"长线被当日内单"的剩余部分。
    # 判据与清单：`full_auto/trend_lane_manager.MID_LANE_ONLY_PATHS`（测试会核对源码一致）。
    _trend_own_pipeline = False
    if _trend_lane_pos:
        try:
            from backend.services.full_auto.trend_lane_manager import (
                note_skipped_paths as _tl_note,
                owns_pipeline as _tl_owns,
            )
            _trend_own_pipeline = bool(_tl_owns())
            if _trend_own_pipeline:
                _tl_note(int(position.get("id") or 0), sym, logger)
        except Exception as _tl2_err:
            logger.debug("[MidLong] 车道流水线开关不可用(按轮99 行为): %s", _tl2_err)

    # ═══ ⑥ 反转 / 无进展离场（规则，每 tick）═══
    # [轮103] 中线专属：长线车道的反转出场由论题失效价 / Chandelier 负责
    rev = {"action": "hold", "channel": "", "reason": ""} if _trend_own_pipeline else _dim_reversal(position, market_summary)
    _sig["exit"] = rev["channel"] or "no"
    if _is_factor_pos:
        # [M1-B] 因子仓不执行 bias_reversal/no_progress 叙事平仓。
        # 唯一因子侧主动离场 = factor_invalidated（最小实现）：路由活跃因子
        # 数跌破 FACTOR_ROUTE_MIN_ACTIVE_FACTORS——开出本仓的信号系统整体
        # 失效（此时路由自身也 hold 停新开），存量仓据此离场。
        _fi_reason = _factor_invalidated_reason()
        if _fi_reason:
            _exec_close(db, account_id=account_id, position=position,
                        reason=f"factor_invalidated: {_fi_reason}", host=host, session=session)
            logger.info(
                "[MidLong] stage=manage symbol=%s 因子仓 factor_invalidated 离场: %s",
                sym, _fi_reason,
            )
            return _summary(f"因子失效离场: {_fi_reason}", action="manage_close")
        if rev["action"] == "close":
            logger.info(
                "[MidLong] stage=manage symbol=%s 因子仓跳过叙事平仓(%s): %s",
                sym, rev["channel"], rev["reason"],
            )
            _sig["exit"] = f"skip_{rev['channel']}_factor_pos"
    elif rev["action"] == "close":
        # [2026-08-23 过度阻止修复] bias_reversal/no_progress 也须尊重 tier
        # min_hold（近 7 天实测 5 笔 -17.31、单笔 -3.46，与 trend_broken 碎平同源：
        # 论点来不及兑现就被叙事平仓）。与 review-close 同一保护口径，
        # 紧急亏损（保证金口径）仍可提前离场。
        _mh_res_rev = _review_min_hold_check(db, position=position, pos_tier=pos_tier,
                                             pnl_pct=pnl_pct, hold_hours=hold_hours,
                                             side=side, sym=sym)
        if not _mh_res_rev.get("ok", True):
            logger.info(
                "[MidLong] stage=manage symbol=%s 反转离场被 min_hold 保护拦截(channel=%s): %s",
                sym, rev["channel"], _mh_res_rev.get("detail", ""),
            )
            _sig["exit"] = f"min_hold_block_{rev['channel']}"
        elif _channel_shadowed(rev["reason"], pos_tier):
            # 通道熔断 shadow：该离场通道近 30 笔 wr<40%，只记录不执行
            logger.info(
                "[MidLong] stage=manage symbol=%s 反转离场被通道熔断拦截(channel=%s): %s",
                sym, rev["channel"], rev["reason"],
            )
            _sig["exit"] = f"breaker_shadow_{rev['channel']}"
        else:
            _exec_close(db, account_id=account_id, position=position,
                        reason=rev["reason"], host=host, session=session)
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
                "direction=skipped pyramid=skipped review=skipped staged_tp=skipped exit=%s reason=%s",
                sym, side, pnl_pct * 100, hold_hours, rev["channel"], rev["reason"],
            )
            return _summary(f"反转离场: {rev['reason']}", action="manage_close")

    # ═══ ⑥b 4h 单周期反转离场（mid 专用，规则，每 tick）═══
    # [2026-09-16 验收轮6] 两日亏损审计（23 笔 / giveback ~303）：探针仓峰值仅
    # +0.2~0.5% ROI，TP/trailing 永不触发；_rule_direction 又要求 4h+1d 双周期
    # 同反才平 → 短期趋势已反转时仍死扛到 SL，止损后还立刻重开同向（churn）。
    # 中线仓「短周期转向即走」：4h 反向 + 持有≥2h + 浮盈≤+0.3%（论点未兑现）→ 离场。
    # 对 factor_route 仓同样生效——原 M1-B 让因子仓跳过一切方向复查，正是死扛根源
    # （当前 5 笔 open 死扛仓全部是 factor_route）。浮盈>+0.3% 的仓交给 trailing/
    # min_roi 管理，不在此砍。通道熔断（wr<40%）与其它出场通道同样适用，可自纠偏。
    _r4_detail = None if _trend_own_pipeline else _mid_4h_reversal_reason(
        sym, position, market_summary,
        pos_tier=pos_tier, side=side, hold_hours=hold_hours, pnl_pct=pnl_pct,
    )
    if _r4_detail:
        if _channel_shadowed("reversal_4h", pos_tier):
            logger.info(
                "[MidLong] stage=manage symbol=%s 4h反转离场被通道熔断拦截: %s",
                sym, _r4_detail,
            )
            _sig["exit"] = "breaker_shadow_reversal_4h"
        else:
            _exec_close(db, account_id=account_id, position=position,
                        reason="reversal_4h", host=host, session=session)
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh "
                "direction=skipped pyramid=skipped review=skipped staged_tp=skipped exit=reversal_4h reason=%s",
                sym, side, pnl_pct * 100, hold_hours, _r4_detail,
            )
            return _summary(f"4h反转离场: {_r4_detail}", action="manage_close")

    # ═══ ⑥c 时间止损（P3 大轮回 2026-09-27，§6.1；mid 专用，规则，每 tick）═══
    # min_roi 弱化（仅盈利后生效）后，浮亏死持仓的指定接管者（§9.2 ⑤）：
    #   8h 且未达 μ（pnl≤0 代理）→ 减半；24h → 全平。
    # 依据：中位持仓 1.7h——8h 仍浮亏且无任何方向信号 = 论点没兑现；轮6 实测
    # 死扛仓是 23 笔 giveback ~303 的主源之一。因子仓同样适用（M1-B 口径：
    # 因子仓只认 SL/TP/**时间**/因子失效）。
    # 回滚：MIDLONG_TIME_STOP_ENABLED=false。
    if (not _trend_own_pipeline) and _cfg_bool("MIDLONG_TIME_STOP_ENABLED", True):
        _ts_act, _ts_reason = _time_stop_decision(
            hold_hours, pnl_pct,
            full_h=_cfg_float("MIDLONG_TIME_STOP_FULL_H", 24.0),
            reduce_h=_cfg_float("MIDLONG_TIME_STOP_REDUCE_H", 8.0),
        )
        if _ts_act == "close":
            if _channel_shadowed("time_stop", pos_tier):
                _sig["exit"] = "breaker_shadow_time_stop"
            else:
                _exec_close(db, account_id=account_id, position=position,
                            reason=_ts_reason, host=host, session=session)
                logger.info(
                    "[MidLong] stage=manage symbol=%s 时间止损全平 hold=%.1fh pnl=%+.1f%%（≥24h 未兑现）",
                    sym, hold_hours, pnl_pct * 100,
                )
                return _summary("时间止损全平(24h 未兑现)", action="manage_close")
        elif _ts_act == "reduce":
            if _channel_shadowed("time_stop", pos_tier):
                _sig["exit"] = "breaker_shadow_time_stop"
            else:
                _exec_reduce(db, account_id=account_id, position=position, ratio=0.5,
                             reason=_ts_reason, host=host, session=session)
                logger.info(
                    "[MidLong] stage=manage symbol=%s 时间止损减半 hold=%.1fh pnl=%+.1f%%（≥8h 未达 μ）",
                    sym, hold_hours, pnl_pct * 100,
                )
                return _summary("时间止损减半(8h 未达 μ)", action="manage_reduce")

    # ═══ ⑤ 分批止盈（规则，每 tick）═══
    # [轮103] 中线专属：长线的分档档位是 8/15/25%（别处声明），
    # 这套 15min 尺度的分批止盈用在长线上就是"过早止盈"。
    staged = ({"action": "hold", "channel": "", "reason": "", "ratio": 0.0}
              if _trend_own_pipeline else
              _dim_staged_tp(db, host=host, session=session, account_id=account_id,
                             position=position, market_summary=market_summary))
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

    # ═══ ④ 受控逆势补仓（F40：行情未反转时替代「小亏全平」）═══
    # [轮103] 中线专属：逆势补仓（摊平）不是趋势车道的工具 —— 趋势车道只做**顺势**滚仓。
    if (not _trend_own_pipeline) and _cfg_bool("MIDLONG_CONTROLLED_DCA_ENABLED", True):
        try:
            _dca = _dim_controlled_dca(
                db, account_id=account_id, position=position, host=host, session=session,
            )
            _sig["dca"] = _dca["channel"] or "no"
            if _dca["action"] == "dca_executed":
                logger.info(
                    "[MidLong] stage=manage symbol=%s pos=%s pnl=%+.1f%% hold=%.1fh dca=%s reason=%s",
                    sym, side, pnl_pct * 100, hold_hours, _dca["channel"], _dca["reasoning"],
                )
                return _summary(f"逆势补仓: {_dca['reasoning']}", action="manage_dca")
        except Exception as _dca_dim_err:
            logger.debug("[MidLong] stage=manage %s 补仓维度异常: %s", sym, _dca_dim_err)

    # ═══ LLM 维度（①②③）节流：复用 exit_state_json.last_trend_review_ts ═══
    # [轮100] 复查节奏按**车道**取（中线默认 4h、长线默认 4h→可独立调）：
    # 中线(12–48h)与长线(3–7天)的时间尺度差 4~14 倍，共用一个节奏必然有一边不合适
    # （4h 对长线是"每 1/40 生命复查一次"）。真源见 config/lane_policy.py。
    _llm_interval = _cfg_int("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC", 900)
    try:
        from backend.config.lane_policy import resolve as _lane_resolve
        _lane_pol = _lane_resolve(
            tier=_tier_of(position), nature=position.get("trade_nature"),
        )
        if _lane_pol is not None and int(getattr(_lane_pol, "review_interval_sec", 0) or 0) > 0:
            _llm_interval = int(_lane_pol.review_interval_sec)
    except Exception as _lane_err:
        logger.debug("[MidLong] 车道复查节奏解析失败(用通用键): %s", _lane_err)
    _last_llm = _last_llm_run_ts.get(_key, 0.0)
    _llm_due = (_now - _last_llm) >= _llm_interval
    # [M1-B] _db_pos/_state 已在出场分流处提前加载，此处只做节流合并
    try:
        _last_llm = max(_last_llm, float(_state.get("last_trend_review_ts", 0) or 0))
        _llm_due = (_now - _last_llm) >= _llm_interval
    except Exception as _e:
        logger.debug("[MidLong] stage=manage %s 节流合并失败: %s", sym, _e)

        # [2026-08-16 P0 修复]「开仓就被平」根因：新仓在 _last_llm_run_ts 无记录、
        # exit_state_json 无 last_trend_review_ts → 首个 manage tick 即触发 LLM
        # 方向复查 → trend_broken 平仓（实测 1~3 分钟内连平 BNB/SOL/VIRTUAL 等）。
        # 首个复查窗口必须从开仓时间起算：新仓至少持有 _llm_interval 秒
        # 才有第一次 LLM 复查，给论点一个兑现窗口。
        _held_sec = _held_hours(position, db) * 3600.0
        if _last_llm <= 0.0 or _held_sec < _llm_interval:
            _llm_due = _held_sec >= _llm_interval

    # ── [2026-09-28 用户指令「这么久了没有滚仓」] 规则直通滚仓**不受 LLM 节流约束** ──
    # 现状（实测）：① `_pyr_direct` 写在 `_llm_due` 早退**之后** ⇒ 长线 4h 一次 LLM 复审，
    # 规则直通永远够不到；② 09-25 凌晨 LLM 反复判 add，又撞上 09-19 滚仓事故残留的
    # 「已加仓3次」快照（phantom rollback 后 DB add_count 已复位 0，门控当时读的是旧快照）。
    # 修复：保证金浮盈 > 直通阈值(默认5%) 且 4h/1d 双周期同向时，**先于节流早退**直接进
    # 5 层门控（门控内仍有 3 次上限 / 逐档盈利门槛 / 冷却，不会无脑加）。
    # 开关 MIDLONG_PYRAMID_RULE_PRETHROTTLE（默认 true）；回滚置 false = 旧行为。
    if _cfg_bool("MIDLONG_PYRAMID_RULE_PRETHROTTLE", True) and not _llm_due:
        try:
            if _pyramid_rule_prethrottle_ready(sym, position, market_summary):
                _ok_pre = _exec_pyramid(
                    db, account_id=account_id, position=position,
                    market_summary=market_summary, host=host,
                    session=session, trading_mode=trading_mode,
                )
                if _ok_pre:
                    _margin_pnl = _pnl_pct_of(position)
                    logger.info(
                        "[MidLong] %s 滚仓执行(规则直通·节流前，保证金浮盈%.1f%%)",
                        sym, _margin_pnl * 100,
                    )
                    return _summary("顺势滚仓执行(规则直通·节流前)", action="manage_pyramid")
        except Exception as _pre_err:
            logger.debug("[MidLong] %s 节流前滚仓检查跳过: %s", sym, _pre_err)

    if not _llm_due:
        logger.debug("[MidLong] stage=manage %s LLM维度节流中(距上次%.0fs)", sym, _now - _last_llm)
        return _summary(f"规则维度已检查({_sig['exit']}/{_sig['staged_tp']})，LLM维度节流中", action="manage_hold")

    # ═══ ① + ③ 方向延续性复查 + TP/SL 调整（LLM）═══
    if _is_factor_pos:
        # [M1-B] 因子仓跳过方向复查（规则 _rule_direction 与 LLM 均不执行）：
        # 复查产生的 trend_broken/trend_weaken 平仓是因子仓碎平主因；因子仓
        # 只认 SL/TP/时间/因子失效。trend_adjustment 的收紧追踪由 staged TP
        # 与既有 trailing 机制承担。
        review = {"action": "hold", "reasoning": "因子仓跳过方向复查(M1)"}
        _review_action = "hold"
        _sig["review"] = "skip_factor_pos"
    else:
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
    # [轮103] 长线车道跳过 ① 复查给出的 close（`direction_close`）：
    # 那是**中线口径**的趋势复查（15min/4h 尺度）—— 长线的出场是论题失效价与 Chandelier。
    # 注意 `_trend_own_pipeline` 为真时 `_review_action` 仍会被计算（有 LLM 成本），
    # 但只有 add 那一支会被采纳（见下方滚仓块）。
    if _review_action == "close" and _trend_own_pipeline:
        logger.info(
            "[MidLong] %s tier=%s 长线车道忽略复查 close(%s) —— 出场交「论题失效价 / Chandelier」（轮103）",
            sym, position.get("timeframe_tier"), _reason_base,
        )
        _sig["exit"] = "skip_review_close_trend_lane"

    if _review_action == "close" and not _trend_own_pipeline:
        # [2026-08-22 M0-11] 复查平仓必须尊重 tier min_hold（mid 12h / long 72h，
        # TIER_PROTECTION_PARAMS 契约），否则 1-4h 内被规则复查碎平。
        # 实测证据（_audit_exit_channels）：swing 的 trend_broken 58 笔均持 1.0h
        # 合计 -13.5、master_running_close 30 笔均持 4.0h 合计 -12.4 —— 全部碎平；
        # 而活到 6h+ 的通道全部为正（dust_cleanup 8.8h +14.2 / breakeven 6h +4.8 /
        # max_hold_timeout 35.4h +4.1 / emergency 19.3h +5.9 / trend_follow 54.4h +29.3）。
        # 设计结构（TP 8.9%/SL 4.6% ⇔ RR≈1.94；trend TP16.5%/SL6.8% ⇔ RR≈2.4）需要
        # 兑现窗口；1-4h 的 4h/1d 指标复查只是开仓噪声，不是论点破坏。
        # 紧急亏损（min_hold_emergency_loss_pct：保证金口径 6%）仍可提前离场。
        _mh_res = _review_min_hold_check(db, position=position, pos_tier=pos_tier,
                                         pnl_pct=pnl_pct, hold_hours=hold_hours,
                                         side=side, sym=sym)
        if not _mh_res.get("ok", True):
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s 复查平仓被 min_hold 保护拦截: %s",
                sym, position.get("id"), _mh_res.get("detail", ""),
            )
            return _summary(f"min_hold 保护: {_mh_res.get('detail', '')}", action="manage_hold")

        if _channel_shadowed("trend_broken", pos_tier):
            # 通道熔断 shadow：trend_broken 通道近 30 笔 wr<40%，只记录不执行
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s 方向破坏离场被通道熔断拦截: %s",
                sym, position.get("id"), _reason_base,
            )
            return _summary(f"通道熔断 shadow(trend_broken): {_reason_base}", action="manage_hold")

        # [2026-09-11 V11] trend_broken 价格闸：浮亏 < 3%（SL6% 的一半）且日线非下行时
        # 不执行平仓。实证 long 组 24 笔 trend_broken 实际均 −4.10（合计 −98.46），
        # 同一批入场政策反事实 +8.13%/笔（胜率 83%）——4h 级复查在噪音区砍仓，
        # 砍在 72h 兑现窗口之前。回滚：MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS=0。
        _tb_ok, _tb_detail = trend_broken_price_gate(position, side=side, tier=pos_tier)
        if not _tb_ok:
            logger.info(
                "[MidLong] stage=manage symbol=%s pos=%s trend_broken 价格闸拦截: %s（%s）",
                sym, position.get("id"), _tb_detail, _reason_base,
            )
            return _summary(f"trend_broken 价格闸: {_tb_detail}", action="manage_hold")

        # [2026-08-26 亏损复盘] "转 mixed" 类复查平仓缓冲：震荡日里 4h 转 mixed
        # 并非结构破坏，浮亏 <2% 时先 hold 观察一档（每仓每天最多 1 次），
        # 避免被反复砍小亏损（8/26 XPL 09:08/09:19 两笔 -3.5 同源）。
        _rb_l = str(_reason_base or "").lower()
        if "mixed" in _rb_l and float(pnl_pct or 0) > -0.02:
            try:
                import json as _json_mb
                from backend.database.models import PaperPosition as _PP_mb
                _row_mb = db.query(_PP_mb).filter(_PP_mb.id == int(position.get("id") or 0)).first()
                _st_mb = {}
                if _row_mb is not None:
                    try:
                        _st_mb = _json_mb.loads(getattr(_row_mb, "exit_state_json", None) or "{}") or {}
                    except Exception:
                        _st_mb = {}
                _skips_mb = int((_st_mb or {}).get("mixed_review_skips") or 0)
                if _skips_mb < 1:
                    _st_mb["mixed_review_skips"] = _skips_mb + 1
                    if _row_mb is not None:
                        _row_mb.exit_state_json = _json_mb.dumps(_st_mb, ensure_ascii=False)
                        try:
                            db.commit()
                        except Exception:
                            try:
                                db.rollback()
                            except Exception:
                                pass
                    logger.info(
                        "[MidLong] stage=manage %s mixed复查缓冲 hold一档(pnl=%+.1f%%): %s",
                        sym, float(pnl_pct or 0) * 100, _reason_base,
                    )
                    return _summary("mixed复查缓冲 hold一档", action="manage_hold")
            except Exception as _mb_err:
                logger.debug("[MidLong] mixed缓冲检查跳过: %s", _mb_err)

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

    # ⛔ [轮99] 趋势车道禁止收紧追踪止损：这正是把趋势仓降级成日内仓的元凶
    # （`SL = 现价 − 2×短周期ATR` ≈1%，一轮正常回撤即收割，实测 4 笔 long 全中）。
    # 车道契约里趋势仓的止损只由 Chandelier 上移（`trend_e1_engine` 日任务）。
    # [轮103] gate 条件扩为 `_trend_own_pipeline`（含回滚开关）。
    if _review_action == "tighten_trailing" and (_trend_lane_pos or _trend_own_pipeline):
        logger.info(
            "[MidLong] %s tier=%s 属趋势车道 → 忽略 tighten_trailing（%s）；"
            "止损只由 Chandelier 结构位上移（轮99）",
            sym, position.get("timeframe_tier"), _reason_base,
        )
        return _summary(f"趋势车道忽略收紧止损请求: {_reason_base}", action="manage_trend_skip_tighten")

    if _review_action == "tighten_trailing":
        _trend_adj = review.get("trend_adjustment") or {}
        _atr_mult = float(_trend_adj.get("trailing_atr_mult") or 0)
        _new_sl = None
        _mark_for_band = 0.0
        if _atr_mult > 0:
            mark = float(position.get("mark_price", 0) or position.get("entry_price", 0) or 0)
            _mark_for_band = mark
            _sym_mkt = market_summary.get(sym) or {}
            _atr_pct = 0.02
            if isinstance(_sym_mkt, dict):
                _atr_pct = float(_sym_mkt.get("volatility_value", 0.02) or 0.02)
            _band = mark * _atr_pct * _atr_mult
            if side == "long":
                _new_sl = mark - _band
            else:
                _new_sl = mark + _band
        # ── [2026-09-18 轮96 修 Fix B] 车道**最小收紧带宽**（抽成可测的纯函数）──
        # 上面那个 `_band = mark × volatility_value × trailing_atr_mult` 用的是
        # **短周期**波动率（ETH/LINK 实测 ≈0.5%）× 2.0 ⇒ 带宽 ≈1% 价格。
        # 对"最短持仓 12h / 设计持仓 3–7 天"的车道来说，1% 回撤是噪音不是反转：
        # 2026-09-18 实测 4 笔 trend_follow + 3 笔 mid 全部在这条线附近被收割
        # （峰值 +1.3%~+6.0%，平仓时 retention 0.62~0.88）。
        # 故设车道下限：收紧后的止损与**现价**距离不得小于 `MIDLONG_TIGHTEN_MIN_BAND_PCT_<TIER>`
        # （long 默认 3%、mid 默认 1%、short 默认 0=不设限）。
        # 只**放松**本次提议（绝不主动下调已有的更高止损），所以不产生"把保护撤掉"的风险。
        if _new_sl and _new_sl > 0 and _mark_for_band > 0:
            _band_tier = str(
                position.get("timeframe_tier")
                or ("long" if nature in ("trend_follow", "position") else "mid")
            ).strip().lower()
            _new_sl, _band_note = clamp_tighten_band(
                side=side, mark=_mark_for_band, new_sl=_new_sl, tier=_band_tier,
            )
            if _band_note:
                logger.info("[MidLong] %s %s", sym, _band_note)
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
    #
    # ⛔ [轮99] 趋势车道**禁止裁量减仓**：实测长线仓被反复 `reduce` 千刀万剐 ——
    # #4659 BTC 减 6 次（size 0.002615→0.000071，剩 2.7%）、#4660 BNB 减 5 次（剩 4.5%）、
    # #4678 ETH 减 3 次（剩 12.5%），峰值分别只有 +2.7%/+0.4%/+0.3%。
    # 车道契约是"失效即全平（规则失效）或跟随 Chandelier"，不是按 40%~50% 反复削仓。
    if _review_action == "reduce" and (_trend_lane_pos or _trend_own_pipeline):
        logger.info(
            "[MidLong] %s tier=%s 属趋势车道 → 忽略裁量减仓（%s）；"
            "出场交「规则失效全平 / Chandelier」（轮99）",
            sym, position.get("timeframe_tier"), _reason_base,
        )
        return _summary(f"趋势车道忽略裁量减仓: {_reason_base}", action="manage_trend_skip_reduce")

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

    ⚠️ [2026-09-18 轮93 P2-12] **当前全仓无任何调用者**（`grep` 全库只有本定义）。
    实际生效的是 `mlto_cycle._trend_one` 里按 symbol 逐个调用的 `manage_position`
    （模式 B 的单仓形态）。本函数**不是**死代码意义上的"不可达"（它是可调用的公开
    入口、逻辑也正确），但把它当成已接线的能力是错的 —— 例如"持仓管理已拆成独立
    调度"这种说法在本函数被真正调用之前不成立。
    若将来接线：请同时更新 `backend/tests/unit/test_dead_entrypoints_20260918.py`
    里钉住"无调用者"的那条断言，避免文档与事实再次分叉。
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
