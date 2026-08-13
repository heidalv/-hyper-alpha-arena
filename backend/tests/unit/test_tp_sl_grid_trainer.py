"""止盈止损网格训练：路径模拟 + 读路径覆盖。"""
from __future__ import annotations

import numpy as np

from backend.services.risk import tp_sl_grid_trainer as m


def test_simulate_path_hits_tp_long():
    close = np.array([100.0, 100.5, 101.0, 102.0, 103.0])
    high = np.array([100.2, 100.8, 101.5, 103.0, 103.5])
    low = np.array([99.8, 100.2, 100.8, 101.5, 102.5])
    ret, reason = m.simulate_path(high, low, close, 0, 1, tp_pct=0.02, sl_pct=0.01, max_bars=10, cost=0.0)
    assert reason == "tp"
    assert abs(ret - 0.02) < 1e-9


def test_simulate_path_hits_sl_long():
    close = np.array([100.0, 99.5, 99.0, 98.0])
    high = np.array([100.2, 99.8, 99.2, 98.5])
    low = np.array([99.5, 98.5, 98.0, 97.0])
    ret, reason = m.simulate_path(high, low, close, 0, 1, tp_pct=0.05, sl_pct=0.02, max_bars=10, cost=0.0)
    assert reason == "sl"
    assert abs(ret + 0.02) < 1e-9


def test_long_tier_has_real_tp_grid():
    """长线必须训真实止盈，禁止再写死 tp_grid=[0]。"""
    spec = m._TIER_SPECS["long"]
    assert all(float(x) > 0 for x in spec["tp_grid"])
    assert float(spec["min_rr"]) >= 1.0


def test_classify_morph_trend_vs_range():
    n = 30
    close_flat = np.full(n, 100.0)
    high_flat = close_flat + 0.2
    low_flat = close_flat - 0.2
    assert m._classify_morph(high_flat, low_flat, close_flat, n - 1, 8) == "range"

    close_up = np.linspace(100.0, 112.0, n)
    high_up = close_up + 0.5
    low_up = close_up - 0.5
    morph = m._classify_morph(high_up, low_up, close_up, n - 1, 8)
    assert morph in ("trend", "breakout")


def test_get_learned_pct_from_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_SL_LEARNED_DIR", str(tmp_path))
    monkeypatch.setenv("RISK_USE_LEARNED_TP_SL", "1")
    m._cache["mtime"] = None
    m._cache["payload"] = None
    payload = {
        "updated_at": "2026-08-12T00:00:00+00:00",
        "by_tier": {
            "short": {"tp_pct": 0.02, "sl_pct": 0.012, "source": "grid_oos"},
            "mid": {"tp_pct": 0.06, "sl_pct": 0.03, "source": "grid_oos"},
            "long": {"tp_pct": 0.12, "sl_pct": 0.06, "source": "grid_oos"},
            "long|trend": {"tp_pct": 0.15, "sl_pct": 0.06, "source": "grid_oos", "shape": "trend"},
            "long|low": {"tp_pct": 0.10, "sl_pct": 0.05, "source": "grid_oos", "shape": "band:low"},
        },
    }
    m.save_learned(payload, path=tmp_path / "latest.json")
    got = m.get_learned_pct("short")
    assert got is not None
    assert abs(got["tp_pct"] - 0.02) < 1e-9
    assert abs(got["sl_pct"] - 0.012) < 1e-9

    assert abs(m.get_learned_pct("long")["tp_pct"] - 0.12) < 1e-9
    assert abs(m.get_learned_pct("long", morph="trend")["tp_pct"] - 0.15) < 1e-9
    assert abs(m.get_learned_pct("long", band="low")["tp_pct"] - 0.10) < 1e-9

    # 旧版 long tp=0 不再被开仓路径采用
    m.save_learned(
        {"by_tier": {"long": {"tp_pct": 0.0, "sl_pct": 0.05, "source": "grid_oos"}}},
        path=tmp_path / "latest.json",
    )
    m._cache["mtime"] = None
    m._cache["payload"] = None
    assert m.get_learned_pct("long") is None

    monkeypatch.setenv("RISK_USE_LEARNED_TP_SL", "0")
    m._cache["mtime"] = None
    m._cache["payload"] = None
    assert m.get_learned_pct("short") is None


def test_compute_initial_uses_learned(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_SL_LEARNED_DIR", str(tmp_path))
    monkeypatch.setenv("RISK_USE_LEARNED_TP_SL", "1")
    monkeypatch.setenv("RISK_P2_ENABLED", "0")
    monkeypatch.setenv("RISK_USE_TIER_TP_SL_V2", "0")
    monkeypatch.setenv("RISK_USE_VOL_BAND_DEFAULTS", "0")
    m._cache["mtime"] = None
    m._cache["payload"] = None
    m.save_learned(
        {
            "by_tier": {
                "mid": {"tp_pct": 0.055, "sl_pct": 0.028, "source": "grid_oos"},
            }
        },
        path=tmp_path / "latest.json",
    )
    from backend.services.full_auto.tp_sl_prices import compute_initial_tp_sl_prices

    tp, sl, src = compute_initial_tp_sl_prices(
        tier="mid",
        action="buy",
        ref_price=100.0,
        atr_pct=0.0,
        sym="",
    )
    assert "learned" in src
    assert abs(tp - 105.5) < 1e-6
    assert abs(sl - 97.2) < 1e-6
