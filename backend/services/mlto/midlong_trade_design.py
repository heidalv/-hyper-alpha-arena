"""中长线交易设计闸门（P1，2026-07-31）。

虚拟币永续语境下的硬规则：
1. Chop 禁开：横盘/弱趋势不做长线
2. Funding 净 RR：持仓成本扣减后盈亏比必须达标
3. ATR 仓位：按权益风险%与止损距离缩放

杠杆不在本模块处理：统一遵守 leverage_authority / 动态杠杆与交易所既定规则，
禁止在此「统一降档」或覆盖周期策略杠杆 cap。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _cfg_bool(name: str, default: bool = True) -> bool:
    try:
        from backend.config import settings
        return bool(getattr(settings, name, default))
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        from backend.config import settings
        return float(getattr(settings, name, default) or default)
    except Exception:
        return default


def _cfg_int(name: str, default: int) -> int:
    try:
        from backend.config import settings
        return int(getattr(settings, name, default) or default)
    except Exception:
        return default


def estimate_atr_1d_pct(ms: Dict[str, Any]) -> Optional[float]:
    """日线 ATR% = ATR/close。优先用现成字段，否则从 recent_klines 估。"""
    if not isinstance(ms, dict):
        return None
    for key in ("atr_1d_pct", "atr_pct_1d"):
        v = ms.get(key)
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            pass
    ind = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
    atr_abs = ind.get("atr") or ind.get("atr_14")
    price = float(ms.get("current_price") or ms.get("price") or ms.get("mark_price") or 0)
    recent = ind.get("recent_klines") or []
    if price <= 0 and isinstance(recent, list) and recent:
        try:
            price = float((recent[-1] or {}).get("close") or 0)
        except Exception:
            price = 0
    try:
        if atr_abs is not None and price > 0:
            return float(atr_abs) / price
    except (TypeError, ValueError):
        pass
    if isinstance(recent, list) and len(recent) >= 15 and price > 0:
        try:
            trs = []
            prev_c = None
            for row in recent[-20:]:
                if not isinstance(row, dict):
                    continue
                h = float(row.get("high") or 0)
                l = float(row.get("low") or 0)
                c = float(row.get("close") or 0)
                if h <= 0 or l <= 0:
                    continue
                if prev_c is None:
                    tr = h - l
                else:
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                trs.append(tr)
                prev_c = c
            if len(trs) >= 10:
                atr = sum(trs[-14:]) / min(14, len(trs))
                return atr / price
        except Exception:
            return None
    return None


def is_chop_regime(
    ms: Dict[str, Any],
    orch: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """判定是否处于不宜开长线的震荡市。

    有明确编排器方向（conf≥0.35）时，不因 ADX 偏低单独禁开（趋势初期 ADX 常低）。
    """
    if not _cfg_bool("MIDLONG_CHOP_GATE_ENABLED", True):
        return False, ""
    ms = ms if isinstance(ms, dict) else {}
    orch = orch if isinstance(orch, dict) else {}

    # [P2-8] 统一震荡判定口径：先引用 RegimeAgent.classify_regime（价格变动
    # + 波动率）的权威 regime。明确趋势（trend）时直接放行——趋势初期 ADX
    # 常偏低，不应因 ADX 单独禁开；避免「classify_regime 判 trend、is_chop
    # 判 chop」的双口径自相矛盾。
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        _reg = classify_regime(ms)
        if _reg.regime == "trend":
            return False, ""
    except Exception:
        pass

    adx_max = _cfg_float("MIDLONG_CHOP_ADX_MAX", 18.0)

    long_bias = str(orch.get("long_bias") or "neutral").lower()
    mid_bias = str(orch.get("mid_bias") or "neutral").lower()
    long_conf = float(orch.get("long_confidence") or orch.get("long_conf") or 0)
    mid_conf = float(orch.get("mid_confidence") or orch.get("mid_conf") or 0)
    has_directional = (
        (long_bias in ("bullish", "bearish", "long", "short") and long_conf >= 0.35)
        or (mid_bias in ("bullish", "bearish", "long", "short") and mid_conf >= 0.35)
    )

    regime = str(ms.get("market_cycle") or "").lower()
    if isinstance(ms.get("regime"), dict):
        regime = regime or str(ms["regime"].get("name") or "").lower()
    else:
        regime = regime or str(ms.get("regime") or "").lower()
    for token in ("sideways", "ranging", "chop", "range"):
        if token in regime and not has_directional:
            return True, f"regime={regime}"

    ind_1d = ms.get("indicators_1d") if isinstance(ms.get("indicators_1d"), dict) else {}
    adx = ms.get("adx_1d")
    if adx is None:
        adx = ind_1d.get("adx")
    try:
        if adx is not None and float(adx) < adx_max and not has_directional:
            return True, f"ADX_1d={float(adx):.1f}<{adx_max:.0f}"
    except (TypeError, ValueError):
        pass

    ema_1d = str(ind_1d.get("ema_trend") or ind_1d.get("trend") or "").lower()
    if (
        long_bias in ("neutral", "")
        and mid_bias in ("neutral", "")
        and ema_1d in ("mixed", "neutral", "")
    ):
        try:
            if adx is None or float(adx) < (adx_max + 5):
                return True, "orch_neutral+ema_mixed"
        except (TypeError, ValueError):
            return True, "orch_neutral+ema_mixed"

    return False, ""


def funding_net_rr_ok(
    *,
    action: str,
    tp_pct: float,
    sl_pct: float,
    funding_rate: Optional[float],
    hold_hours: Optional[float] = None,
) -> Tuple[bool, float, str]:
    """扣减预计资金费率后的净盈亏比闸门。"""
    if not _cfg_bool("MIDLONG_FUNDING_GATE_ENABLED", True):
        return True, 0.0, "funding_gate_off"
    sl = float(sl_pct or 0)
    tp = float(tp_pct or 0)
    if sl <= 0 or tp <= 0:
        return False, 0.0, "tp_or_sl_missing"
    min_rr = _cfg_float("MIDLONG_MIN_NET_RR", 2.0)
    hold_h = float(hold_hours if hold_hours is not None else _cfg_float("MIDLONG_FUNDING_HOLD_HOURS", 72.0))
    periods = max(1.0, hold_h / 8.0)
    # [2026-08-15 消费端验收] funding_rate 缺失（None）时原按 0 处理 → 成本 0
    # → 净 RR 闸门被静默绕过（0 成本开仓）。现改为保守估计费率
    #（MIDLONG_FUNDING_UNKNOWN_RATE，默认 0.01%/8h，可配）并在原因中显式
    # 标注「估计口径」，绝不把缺失伪装成 0 成本。
    _funding_unknown = False
    if funding_rate is None:
        fr = _cfg_float("MIDLONG_FUNDING_UNKNOWN_RATE", 0.0001)
        _funding_unknown = True
    else:
        fr = float(funding_rate or 0.0)
    _unknown_tag = f"（funding 缺失，按保守估计 {fr:.4%}/8h）" if _funding_unknown else ""
    act = (action or "").lower()
    if act in ("buy", "long"):
        cost = fr * periods
    elif act in ("sell", "short"):
        cost = (-fr) * periods
    else:
        cost = abs(fr) * periods
    abs_warn = _cfg_float("MIDLONG_FUNDING_ABS_WARN", 0.0005)
    eff_min = min_rr
    if abs(fr) >= abs_warn:
        eff_min = max(min_rr, min_rr + 0.25)

    net_tp = tp - max(0.0, cost)
    if net_tp <= 0:
        return False, 0.0, f"funding_eats_tp cost={cost:.4%} tp={tp:.2%}{_unknown_tag}"
    net_rr = net_tp / sl
    if net_rr < eff_min:
        return False, net_rr, f"net_rr={net_rr:.2f}<{eff_min:.2f} (funding_cost={cost:.4%}){_unknown_tag}"
    return True, net_rr, f"net_rr={net_rr:.2f}{_unknown_tag}"


def atr_size_multiplier(
    *,
    sl_pct: float,
    atr_1d_pct: Optional[float],
    risk_pct: Optional[float] = None,
) -> Tuple[float, str]:
    """按 ATR/止损距离给出仓位乘子（只缩不放大，夹在 0.25~1.0）。"""
    if not _cfg_bool("MIDLONG_ATR_SIZING_ENABLED", True):
        return 1.0, "atr_sizing_off"
    sl = float(sl_pct or 0)
    if sl <= 0:
        return 1.0, "no_sl"
    atr = float(atr_1d_pct or 0)
    atr_mult = _cfg_float("MIDLONG_ATR_SL_MULT", 1.5)
    risk = float(risk_pct if risk_pct is not None else _cfg_float("MIDLONG_RISK_PCT", 0.01))
    ref = atr * atr_mult if atr > 0 else 0.0
    if ref <= 0:
        if sl > 0.08:
            return max(0.35, 0.08 / sl), f"wide_sl={sl:.2%}"
        return 1.0, "no_atr"
    mult = min(1.0, ref / sl)
    if risk > 0 and risk < 0.01:
        mult *= risk / 0.01
    mult = max(0.25, min(1.0, mult))
    return mult, f"atr={atr:.2%} ref_sl={ref:.2%} trade_sl={sl:.2%} →×{mult:.2f}"


def apply_structure_atr_floor(
    *,
    sl_pct: float,
    atr_1d_pct: Optional[float],
) -> Tuple[float, str]:
    """止损至少覆盖 ATR×mult，避免长线被日噪音波扫。"""
    atr = float(atr_1d_pct or 0)
    if atr <= 0:
        return float(sl_pct or 0), "no_atr"
    floor = atr * _cfg_float("MIDLONG_ATR_SL_MULT", 1.5)
    sl = float(sl_pct or 0)
    if floor > sl:
        return floor, f"sl {sl:.2%}→{floor:.2%} (ATR×mult floor)"
    return sl, "ok"
