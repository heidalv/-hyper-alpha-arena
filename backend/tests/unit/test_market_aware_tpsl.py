# -*- coding: utf-8 -*-
"""短线行情出场管家：不同行情数字要能拉开，旧窄夹幅可回滚。"""
from __future__ import annotations

import pytest


def _md(*, atr=0.01, chg_24h=0.0, chg_1h=0.0, price=100.0, **extra):
    row = {
        "price": price,
        "volatility_value": atr,
        "atr_pct": atr,
        "volatility_pct": atr,
        "price_change_24h_pct": chg_24h,
        "price_change_1h_pct": chg_1h,
        "klines": None,
        "symbol": extra.pop("symbol", "BTC"),
    }
    row.update(extra)
    return row


def test_high_vol_trend_wider_than_low_vol_range():
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator

    hi = _md(atr=0.024, chg_24h=5.0, chg_1h=1.0)
    lo = _md(atr=0.006, chg_24h=0.2, chg_1h=-0.1)
    sl_hi, tp_hi, _, _ = structure_stop_calculator.compute_sl_tp(
        hi, side="long", entry=100.0, symbol="SOL",
    )
    sl_lo, tp_lo, _, _ = structure_stop_calculator.compute_sl_tp(
        lo, side="long", entry=100.0, symbol="BTC",
    )
    assert sl_hi > sl_lo + 0.004, f"高波动趋势止损应明显宽于低波震荡: {sl_hi} vs {sl_lo}"
    assert tp_hi > tp_lo, f"高波动止盈应更远: {tp_hi} vs {tp_lo}"
    assert sl_hi > 0.0115, f"管家不应再把 2.4% ATR 压回 1.15%: {sl_hi}"
    assert sl_hi <= 0.030
    # [2026-09-02 P2.2] TP 上限 4.0%→5.5%、最低 RR 1.3→2.0。
    # 依据：实测 short tier 盈亏比 1.042 / 胜率 41.4%，打平需 1.417；
    # 回放（做多+pwin>=0.55, 2680条, SL1.1%）RR1.3=+25.4bp → RR2.3=+44.0bp。
    assert tp_hi <= 0.055
    assert tp_hi / sl_hi >= 2.0 - 1e-9, (
        f"高波动趋势单 RR={tp_hi / sl_hi:.3f} 未达新下限 2.0"
    )
    assert hi["_tpsl_plan"]["playbook"] == "trend_trail"
    assert lo["_tpsl_plan"]["playbook"] == "range_hard_tp"
    assert "趋势" in hi["_tpsl_plan"]["reason"]


def test_extreme_tighter_than_trend():
    from backend.services.exit.market_aware_tpsl import plan_scalp_tp_sl

    trend = plan_scalp_tp_sl(
        _md(atr=0.02, chg_24h=5.0, chg_1h=1.0), side="long", entry=100.0, atr_pct=0.02,
    )
    extreme = plan_scalp_tp_sl(
        _md(atr=0.02, chg_24h=15.0, chg_1h=6.0), side="long", entry=100.0, atr_pct=0.02,
    )
    assert extreme.playbook == "extreme_lock"
    assert trend.playbook == "trend_trail"
    assert extreme.tp_pct < trend.tp_pct
    assert extreme.sl_pct <= trend.sl_pct + 1e-12


def test_funding_against_shrinks_tp():
    from backend.services.exit.market_aware_tpsl import plan_scalp_tp_sl

    base = _md(atr=0.012, chg_24h=5.0, chg_1h=1.0)
    pay = _md(atr=0.012, chg_24h=5.0, chg_1h=1.0, funding_rate=0.0008)
    a = plan_scalp_tp_sl(base, side="long", entry=100.0, atr_pct=0.012)
    b = plan_scalp_tp_sl(pay, side="long", entry=100.0, atr_pct=0.012)
    assert b.tp_pct < a.tp_pct
    assert "资金费" in b.reason


def test_short_side_prices_and_reason_logged():
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator

    md = _md(atr=0.01, chg_24h=-5.0, chg_1h=-1.0)
    sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
        md, side="short", entry=100.0, symbol="ETH",
    )
    assert sl_price > 100.0 > tp_price
    assert abs(sl_price - 100 * (1 + sl_pct)) < 1e-8
    assert abs(tp_price - 100 * (1 - tp_pct)) < 1e-8
    assert md["_tpsl_plan"]["reason"]
    assert md["_tpsl_plan"]["side"] == "short"


def test_legacy_narrow_clamp_still_available():
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator

    md = _md(atr=0.024, chg_24h=5.0, chg_1h=1.0)
    sl_pct, tp_pct, _, _ = structure_stop_calculator.compute_sl_tp(
        md, side="long", entry=100.0, market_aware=False,
    )
    assert sl_pct == pytest.approx(0.0115)
    assert tp_pct == pytest.approx(0.015)


def test_planner_off_uses_legacy(monkeypatch):
    import backend.config.settings as settings_mod
    from backend.services.scalp.structure_stop_calculator import structure_stop_calculator

    monkeypatch.setattr(settings_mod, "SCALP_MARKET_AWARE_TPSL", False)
    md = _md(atr=0.024, chg_24h=5.0, chg_1h=1.0)
    sl_pct, _, _, _ = structure_stop_calculator.compute_sl_tp(
        md, side="long", entry=100.0,
    )
    assert sl_pct == pytest.approx(0.0115)


def test_rr_floor_after_clamp():
    from backend.services.exit.market_aware_tpsl import plan_scalp_tp_sl

    plan = plan_scalp_tp_sl(
        _md(atr=0.008, chg_24h=0.0, chg_1h=0.0),
        side="long", entry=50.0, atr_pct=0.008,
    )
    assert plan.sl_pct >= 0.005
    assert plan.tp_pct / plan.sl_pct >= 1.3 - 1e-9


def test_apply_market_overlays_funding_and_weekend():
    from backend.services.exit.market_aware_tpsl import apply_market_overlays

    sl, tp, notes = apply_market_overlays(
        0.02, 0.04, side="long",
        market_data={"funding_rate": 0.0008, "is_weekend": True},
        sl_min=0.01, sl_max=0.08, tp_min=0.02, tp_max=0.10,
    )
    assert sl > 0.02  # 周末放宽止损
    assert tp < 0.04  # 资金费作对收近止盈
    assert any("资金费" in n for n in notes)
    assert any("周末" in n for n in notes)


def test_midlong_shares_overlays():
    from backend.services.mid_long_structure_stop import mid_long_structure_stop

    base = {"volatility_value": 0.02, "price": 100.0, "klines": None}
    sl1, tp1, *_ = mid_long_structure_stop.compute(
        symbol="BTC", market_data=dict(base), side="long", entry=100.0,
    )
    sl2, tp2, *_ = mid_long_structure_stop.compute(
        symbol="BTC",
        market_data={**base, "funding_rate": 0.0008},
        side="long", entry=100.0,
    )
    assert tp2 < tp1
    assert sl2 == pytest.approx(sl1)
