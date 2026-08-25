"""sizing_overlay 单测（U5 计算层）。"""
from backend.services.mlto.sizing_overlay import (
    compute_final_multiplier, consensus_discount, credit_factor,
    regime_mult, correlation_penalty, vol_target_scale, committee_hint_to_mult,
)


def test_consensus_discount():
    assert consensus_discount(0.8) == 1.0
    assert abs(consensus_discount(0.3) - 0.8) < 1e-9
    assert abs(consensus_discount(0.0) - 0.5) < 1e-9


def test_credit_factor():
    assert credit_factor(1.0) == 1.0
    assert credit_factor(0.0) == 0.7


def test_regime_mult():
    assert regime_mult("high_vol") == 0.5
    assert regime_mult("trending_up") == 1.0
    assert abs(regime_mult("ranging", vol_ratio=2.0) - 0.8 * 0.7) < 1e-9


def test_correlation_penalty():
    assert correlation_penalty(3) == 1.0
    assert correlation_penalty(4) == 0.85
    assert correlation_penalty(10) == 0.5


def test_vol_target_scale():
    assert vol_target_scale(60.0, 30.0) == 0.5
    assert vol_target_scale(15.0, 30.0) == 1.2


def test_committee_hint():
    assert committee_hint_to_mult("pause") == 0.0
    assert committee_hint_to_mult("increase") == 1.2
    assert committee_hint_to_mult("keep") == 1.0


def test_composite():
    r = compute_final_multiplier(
        kelly_share=0.1, consensus_confidence=0.3, credit=0.8,
        regime="high_vol", n_same_direction=4, realized_vol_pct=60.0,
        budget_hint="reduce",
    )
    b = r["breakdown"]
    expect = 0.1 * 0.8 * b["credit_factor"] * 0.5 * 0.85 * 0.5 * 0.5
    assert abs(r["final"] - expect) < 1e-6
    assert len(b) == 7
