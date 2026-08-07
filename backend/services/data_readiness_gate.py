"""
数据就绪门控 — 禁止无 K 线 / 无真实指标 / 无 LLM 时伪造方向并开仓。

原则：
1. 没数据 → wait/hold，靠 paper_trading_engine 已有 SL/TP 管仓，不瞎编 RSI/置信度。
2. 规则回退 / LLM 降级 → 禁止 buy/sell（仅 hold/close/reduce 管理已有仓）。
3. 编排器 enter 必须 snapshot 审计通过。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

OPEN_ACTIONS = frozenset({"buy", "sell", "pyramid", "dca"})
MANAGE_ACTIONS = frozenset({"hold", "close", "reduce", "adjust_sl", "adjust_tp"})

REQUIRED_KLINE_PERIODS = ("1h", "4h")
PREFERRED_KLINE_PERIODS = ("5m", "15m", "1h", "4h", "1d")


@dataclass
class DataReadinessReport:
    symbol: str
    trading_ready: bool = False
    price_ok: bool = False
    klines_ok: bool = False
    indicators_ok: bool = False
    derivatives_ok: bool = False
    planning_ok: bool = False
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.trading_ready:
            return "数据就绪"
        return "缺失:" + ",".join(self.missing) if self.missing else "数据未就绪"


def indicators_are_real(ind: Optional[Dict[str, Any]]) -> bool:
    """拒绝默认填充的 RSI=50 + MACD=0 假指标，以及 NaN 占位指标。"""
    import math
    if not ind:
        return False
    rsi = float(ind.get("rsi", 50) or 50)
    macd = float(ind.get("macd", 0) or 0)
    close = float(ind.get("close", 0) or 0)
    if close <= 0:
        return False
    # [2026-07-10] 技术指标函数在数据不足时改返回 NaN（原返回 50/0 占位）。
    # 任何关键指标为 NaN 都说明数据不足、指标未算出 → 视为不可用。
    if math.isnan(rsi) or math.isnan(macd) or math.isnan(close):
        return False
    # 典型「未计算指标」占位（兼容旧版返回 50/0 的路径）
    if abs(rsi - 50.0) < 0.01 and abs(macd) < 1e-9:
        adx = float(ind.get("adx", 0) or 0)
        adx4 = float(ind.get("adx_4h", 0) or 0)
        if (adx <= 0 or math.isnan(adx)) and (adx4 <= 0 or math.isnan(adx4)):
            return False
    return True


def assess_symbol_data(
    symbol: str,
    *,
    snapshot: Any = None,
    market_info: Optional[Dict[str, Any]] = None,
) -> DataReadinessReport:
    """评估单币种是否允许做方向判定与开仓。"""
    sym = (symbol or "").upper()
    rep = DataReadinessReport(symbol=sym)

    info = market_info if isinstance(market_info, dict) else {}

    price = float(info.get("current_price", 0) or 0)
    if price <= 0 and snapshot and getattr(snapshot, "markets", None):
        m = snapshot.markets.get(sym) or snapshot.markets.get(symbol)
        if m:
            price = float(getattr(m, "price", 0) or 0)
    if price <= 0 and snapshot and getattr(snapshot, "indicators", None):
        price = float((snapshot.indicators.get(sym) or snapshot.indicators.get(symbol) or {}).get("close", 0) or 0)
    rep.price_ok = price > 0
    if not rep.price_ok:
        rep.missing.append("price")

    kline_tfs: Set[str] = set()
    if snapshot and getattr(snapshot, "klines", None):
        for (s, tf) in snapshot.klines.keys():
            if str(s).upper() == sym:
                kline_tfs.add(tf)
    for tf in REQUIRED_KLINE_PERIODS:
        if tf not in kline_tfs:
            rep.missing.append(f"kline_{tf}")
    rep.klines_ok = all(tf in kline_tfs for tf in REQUIRED_KLINE_PERIODS)

    ind = {}
    if snapshot and getattr(snapshot, "indicators", None):
        ind = snapshot.indicators.get(sym) or snapshot.indicators.get(symbol) or {}
    rep.indicators_ok = indicators_are_real(ind)
    if not rep.indicators_ok:
        rep.missing.append("indicators")

    deriv = {}
    if snapshot and getattr(snapshot, "derivatives_snapshot", None):
        deriv = snapshot.derivatives_snapshot.get(sym) or snapshot.derivatives_snapshot.get(symbol) or {}
    if info.get("derivatives_signal"):
        rep.derivatives_ok = True
    elif deriv and (deriv.get("oi_total") or deriv.get("funding_rate") is not None):
        rep.derivatives_ok = True
    else:
        rep.warnings.append("derivatives_weak")

    if snapshot and getattr(snapshot, "per_symbol_planning", None):
        rep.planning_ok = sym in snapshot.per_symbol_planning or symbol in snapshot.per_symbol_planning
    elif info.get("market_cycle") and info.get("market_cycle") not in ("unknown", "?", ""):
        rep.planning_ok = True
    if not rep.planning_ok:
        rep.warnings.append("long_planning_missing")

    try:
        from backend.config.settings import TRADING_DATA_MODE
        _mode = TRADING_DATA_MODE
    except Exception:
        _mode = "standard"

    explicit = info.get("data_reliable")
    if explicit is False and not rep.price_ok:
        rep.trading_ready = False
        if "data_reliable_false" not in rep.missing:
            rep.missing.append("data_reliable_false")
        return rep
    if explicit is False and rep.price_ok:
        rep.warnings.append("ignored_stale_data_reliable_false")

    comp = info.get("data_completeness") if isinstance(info.get("data_completeness"), dict) else {}
    if _mode == "strict" and comp and comp.get("ok") is False:
        rep.trading_ready = False
        for m in comp.get("missing") or []:
            if m not in rep.missing:
                rep.missing.append(m)
        return rep
    if comp and comp.get("ok") is False:
        for m in comp.get("missing") or []:
            if m not in rep.warnings:
                rep.warnings.append(f"audit:{m}")

    if _mode == "strict":
        rep.trading_ready = rep.price_ok and rep.klines_ok and rep.indicators_ok
    else:
        rep.trading_ready = rep.price_ok and rep.klines_ok
        if not rep.indicators_ok:
            rep.warnings.append("indicators_weak")
    return rep


def allow_open_action(
    symbol: str,
    action: str,
    *,
    snapshot: Any = None,
    market_info: Optional[Dict[str, Any]] = None,
    source: str = "",
) -> Tuple[bool, str]:
    """是否允许 buy/sell 类开仓。不允许则返回原因。"""
    act = (action or "").lower()
    if act not in OPEN_ACTIONS:
        return True, ""
    rep = assess_symbol_data(symbol, snapshot=snapshot, market_info=market_info)
    if rep.trading_ready:
        return True, ""
    msg = f"[数据门控] {symbol} 禁止{act}: {rep.summary()} (source={source})"
    logger.warning(msg)
    return False, msg


def strip_open_actions(
    decisions: List[Dict],
    *,
    snapshot: Any = None,
    market_summary: Optional[Dict[str, Any]] = None,
    reason_prefix: str = "数据不可用",
) -> List[Dict]:
    """将不允许的开仓动作改为 hold。"""
    out: List[Dict] = []
    ms = market_summary or {}
    for dec in decisions or []:
        if not isinstance(dec, dict):
            continue
        d = dict(dec)
        sym = (d.get("symbol") or "").upper()
        act = (d.get("action") or "").lower()
        info = ms.get(sym) or ms.get(d.get("symbol")) or {}
        ok, why = allow_open_action(
            sym, act, snapshot=snapshot, market_info=info, source=reason_prefix,
        )
        if not ok and act in OPEN_ACTIONS:
            d["action"] = "hold"
            d["confidence"] = min(int(d.get("confidence", 0) or 0), 15)
            prev = (d.get("reasoning") or "")[:200]
            d["reasoning"] = f"{reason_prefix}→hold | {why} | 原:{prev}"
            d["_blocked_by"] = "data_readiness_gate"
        out.append(d)
    return out


def gate_orchestrator_decision(decision: Any, snapshot: Any, symbol: str) -> Any:
    """数据不足时编排器不得 enter / 不得给出可交易方向。"""
    rep = assess_symbol_data(symbol, snapshot=snapshot)
    try:
        from backend.config.settings import TRADING_DATA_MODE
        _ok = rep.trading_ready if TRADING_DATA_MODE == "strict" else (rep.price_ok and rep.klines_ok)
    except Exception:
        _ok = rep.price_ok and rep.klines_ok
    if _ok:
        return decision
    # 修复（2026-06-26）：原 gate 清空所有 slots，导致长线 TrendAgent 永远不触发。
    # 现保留长线 slots（长线独立触发），只清空中短线（短线数据要求更严格）。
    decision.final_action = "wait"
    decision.final_side = ""
    decision.final_position_pct = 0.0
    decision.allowed_direction = "none"
    # 只清空中短线 slots，保留长线
    _long_slots = [s for s in decision.recommended_slots if s == "long"]
    decision.recommended_slots = _long_slots
    decision.slot_actions = {k: v for k, v in decision.slot_actions.items() if k == "long"} if hasattr(decision, 'slot_actions') else {}
    decision.reasoning = (
        f"数据未就绪，禁止编排器开仓/定向（长线独立触发保留） | {rep.summary()} | "
        f"已有持仓仍由 SL/TP/风控托管"
    )
    for view in (decision.long_view, decision.mid_view, decision.short_view):
        if rep.missing:
            view.details = (view.details or "") + " | DATA_GATE:blocked"
    return decision


def is_rule_fallback_decision(master: Dict) -> bool:
    overall = (master or {}).get("overall_assessment", "") or ""
    decisions = (master or {}).get("decisions") or []
    if "规则回退" in overall or "LLM不可用" in overall:
        return True
    if decisions and all(
        str(d.get("reasoning", "")).startswith(("[规则", "规则回退", "LLM不可用"))
        for d in decisions if isinstance(d, dict)
    ):
        return True
    return False


def strip_rule_fallback_opens(master: Dict) -> Dict:
    """规则/LLM 降级：只允许 hold/close/reduce，禁止假开仓。"""
    if not master:
        return master
    m = dict(master)
    decs = []
    for d in m.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        dd = dict(d)
        act = (dd.get("action") or "").lower()
        if act in OPEN_ACTIONS:
            dd["action"] = "hold"
            dd["confidence"] = min(int(dd.get("confidence", 0) or 0), 10)
            dd["reasoning"] = (
                "LLM/规则降级禁止开仓，仅观望；持仓由止盈止损与风控管理 | "
                + (dd.get("reasoning") or "")[:120]
            )
            dd["_blocked_by"] = "no_llm_no_fake_open"
        decs.append(dd)
    m["decisions"] = decs
    m["overall_assessment"] = (
        (m.get("overall_assessment") or "")
        + " | [门控] 无LLM/规则降级，禁止新开仓"
    )
    return m
