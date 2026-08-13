"""短线因子排除类别 — Loop/Router 共用常量单测。"""
from backend.services.factor_engine.base_factors import FactorCategory
from backend.services.scalp.scalp_factor_exclude import (
    get_scalp_factor_exclude_categories,
    scalp_exclude_pattern_enabled,
)


def test_scalp_exclude_default_on(monkeypatch):
    monkeypatch.delenv("SCALP_EXCLUDE_PATTERN", raising=False)
    assert scalp_exclude_pattern_enabled() is True
    excl = get_scalp_factor_exclude_categories()
    assert excl is not None
    assert FactorCategory.PATTERN in excl
    assert FactorCategory.BEHAVIORAL in excl


def test_scalp_exclude_can_disable(monkeypatch):
    monkeypatch.setenv("SCALP_EXCLUDE_PATTERN", "0")
    assert scalp_exclude_pattern_enabled() is False
    assert get_scalp_factor_exclude_categories() is None


def test_router_passes_exclude_into_compute(monkeypatch):
    """Router 回退重算路径必须带 exclude_categories（与 loop 对齐）。"""
    from backend.services.scalp_factor_router import ScalpFactorRouter
    import pandas as pd

    captured = {}

    def _fake_compute(klines, md, exclude_categories=None, **kwargs):
        captured["exclude"] = exclude_categories
        return {"momentum": 0.1}

    class _CS:
        direction = 0.4
        strength = 0.5

    monkeypatch.setattr(
        "backend.services.factor_engine.base_factors.factor_engine.compute_all_factors",
        _fake_compute,
    )
    monkeypatch.setattr(
        "backend.services.factor_engine.factor_evaluation_pipeline.factor_pipeline.compute_weighted_signals",
        lambda *a, **k: _CS(),
    )
    monkeypatch.setenv("SCALP_EXCLUDE_PATTERN", "1")

    r = ScalpFactorRouter()
    # 不给 factor_signal，逼出 Router 内部重算
    df = pd.DataFrame(
        {
            "open": [1.0] * 30,
            "high": [1.1] * 30,
            "low": [0.9] * 30,
            "close": [1.0] * 30,
            "volume": [100.0] * 30,
        }
    )
    md = {"price": 1.0, "klines": df, "indicators": {"rsi": 50}}
    r.evaluate("BTC", md)
    assert captured.get("exclude") is not None
    assert FactorCategory.PATTERN in captured["exclude"]
