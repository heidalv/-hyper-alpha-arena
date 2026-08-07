"""ML 全激活接线回归 — #4/#10/#12/#17/#18 主路径。"""
import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def test_ml_activation_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("ML_PIPELINE_ENABLED", "false")
    from backend.services.ml import activation_service

    activation_service._last_run_mono = 0.0
    r = activation_service.run_ml_activation_tick("sess", 1, is_maintenance=True, force=True)
    assert r.get("skipped") is True


def test_build_ml_feature_frame_shape():
    from backend.services.ml.activation_service import build_ml_feature_frame

    idx = pd.date_range("2026-01-01", periods=50, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": np.linspace(100, 110, 50), "volume": 1000.0},
        index=idx,
    )
    feat = build_ml_feature_frame(df)
    assert len(feat) == 50
    assert "ret_5" in feat.columns
    assert "options_skew" in feat.columns


def test_inject_deribit_disabled_passthrough(monkeypatch):
    monkeypatch.setenv("DERIBIT_OPTIONS_ENABLED", "false")
    from backend.services.factor_engine.factor_bridge import inject_deribit_into_klines

    df = pd.DataFrame({"close": [1.0, 2.0]})
    out = inject_deribit_into_klines(df, "BTC")
    assert list(out.columns) == ["close"]
    assert len(out) == 2


def test_ddgda_reweight_uniform_when_disabled(monkeypatch):
    monkeypatch.setenv("DDGDA_ENABLED", "false")
    from backend.services.ml.activation_service import _ddgda_sample_weights

    hist = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    w = _ddgda_sample_weights(hist, "BTC")
    assert len(w) == 3
    assert np.allclose(w, 1.0)


def test_factor_pipeline_hybrid_blend_with_mock_learned(monkeypatch):
    monkeypatch.setenv("FACTOR_WEIGHTING_MODE", "hybrid")
    monkeypatch.setenv("HYBRID_LEARNED_BLEND", "0.5")

    from backend.services.factor_engine.base_factors import FactorCategory, FactorValue
    from backend.services.factor_engine.factor_evaluation_pipeline import FactorEvaluationPipeline

    class _MockLearned:
        model = object()
        feature_columns = ["rsi"]

        def predict_score(self, _df):
            return pd.Series([0.2])

    p = FactorEvaluationPipeline()
    p._learned = _MockLearned()
    fv = {
        "rsi": FactorValue(
            name="RSI", category=FactorCategory.MOMENTUM, value=30.0, normalized=-0.4, has_data=True,
        ),
    }
    cs = p.compute_weighted_signals(fv, {"symbol": "BTC", "regime": "trend"})
    assert cs is not None
    assert -1.0 <= cs.direction <= 1.0
    assert cs.strength >= 0.0


def test_run_ml_activation_starts_background_thread(monkeypatch):
    monkeypatch.setenv("ML_PIPELINE_ENABLED", "true")
    import backend.services.ml.activation_service as act

    act._last_run_mono = 0.0
    with act._lock:
        act._stats["in_flight"] = False

    calls = []

    def _fake_worker(session_id, tick):
        calls.append((session_id, tick))

    monkeypatch.setattr(act, "_activation_worker", _fake_worker)
    r = act.run_ml_activation_tick("abc", 9, is_maintenance=True, force=True)
    assert r.get("started") is True
    import time
    time.sleep(0.05)
    assert calls == [("abc", 9)]
