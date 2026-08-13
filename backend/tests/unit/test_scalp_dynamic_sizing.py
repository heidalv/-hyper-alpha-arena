from backend.services.full_auto.scalp_sizing import compute_scalp_dynamic_notional


def test_scalp_sizing_middle_band():
    """中间档：约 11–28U @442 权益，不是 4U 灰尘，也不是 40U。"""
    eq = 442.0
    weak = compute_scalp_dynamic_notional(
        eq,
        confidence=0.45,
        sl_pct=0.035,
        volatility_pct=0.03,
        base_size_pct=0.50,
        min_margin_pct=0.025,
    )
    strong = compute_scalp_dynamic_notional(
        eq,
        confidence=0.88,
        sl_pct=0.012,
        volatility_pct=0.01,
        base_size_pct=0.50,
        min_margin_pct=0.025,
    )
    assert weak["margin"] >= eq * 0.025 * 0.95  # ~11U 下限
    assert weak["margin"] < 18
    assert strong["margin"] > weak["margin"]
    assert strong["margin"] < 32  # 强信号也不该到 40U
    assert abs(strong["notional"] / 10 - strong["margin"]) < 0.02


def test_scalp_sizing_floor_not_dust():
    out = compute_scalp_dynamic_notional(
        442.0,
        confidence=0.4,
        sl_pct=0.05,
        volatility_pct=0.06,
        base_size_pct=0.2,
        min_margin_pct=0.025,
    )
    assert out["margin"] >= 10.0
    assert out["margin"] < 15.0
