"""短线动态仓位：抬高整体，但按置信度/止损/波动拉开差距。"""
from __future__ import annotations


def compute_scalp_dynamic_notional(
    equity: float,
    *,
    base_size_pct: float = 0.50,
    confidence: float = 0.6,
    sl_pct: float = 0.012,
    volatility_pct: float = 0.015,
    size_mult: float = 1.0,
    leverage: int = 10,
    min_margin_pct: float = 0.025,
    max_trade_risk_pct: float = 0.03,
) -> dict:
    equity = max(float(equity or 0), 0.0)
    if equity <= 0:
        return {"notional": 0.0, "margin": 0.0, "skipped": True, "reason": "no_equity"}

    conf_n = confidence / 100.0 if confidence > 1.5 else confidence
    conf_n = max(0.35, min(0.95, float(conf_n or 0.5)))
    q_mult = 0.55 + (conf_n - 0.35) / (0.95 - 0.35) * 0.65

    sl = max(0.005, min(0.08, float(sl_pct or 0.012)))
    sl_mult = max(0.50, min(1.30, 0.012 / sl))

    vol = max(0.004, float(volatility_pct or 0.015))
    if vol < 0.010:
        vol_mult = 1.15
    elif vol < 0.020:
        vol_mult = 1.00
    elif vol < 0.030:
        vol_mult = 0.78
    else:
        vol_mult = 0.55

    base = equity * max(0.05, min(3.0, float(base_size_pct)))
    notional = base * q_mult * sl_mult * vol_mult * max(0.3, min(1.0, float(size_mult)))

    lev = max(1, int(leverage or 10))
    min_notional = equity * max(0.01, min(0.20, float(min_margin_pct))) * lev
    floored = False
    if notional < min_notional:
        notional = min_notional
        floored = True

    risk_cap = max(0.01, min(0.08, float(max_trade_risk_pct)))
    hard = equity * risk_cap / sl
    capped = False
    if notional > hard:
        notional = hard
        capped = True

    skipped = notional < min_notional * 0.85
    return {
        "notional": round(notional, 2),
        "margin": round(notional / lev, 2),
        "q_mult": round(q_mult, 3),
        "sl_mult": round(sl_mult, 3),
        "vol_mult": round(vol_mult, 3),
        "floored": floored,
        "capped": capped,
        "skipped": skipped,
        "reason": "below_floor_after_risk" if skipped else "ok",
    }
