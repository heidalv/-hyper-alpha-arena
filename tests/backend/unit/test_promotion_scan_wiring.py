"""晋升门扫描接线回归。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_get_effective_learned_blend_by_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMOTION_GATE_ENABLED", "true")
    reg = tmp_path / "reg.json"
    monkeypatch.setattr(
        "backend.services.promotion_scan_service.REGISTRY_PATH",
        str(reg),
    )
    from backend.services.promotion_scan_service import (
        apply_promotion_stage,
        get_effective_learned_blend,
    )

    assert get_effective_learned_blend() == 0.0
    apply_promotion_stage("ml_learned_weighting", "canary")
    assert get_effective_learned_blend() == pytest.approx(0.22)
    apply_promotion_stage("ml_learned_weighting", "full")
    assert get_effective_learned_blend() == pytest.approx(0.45)


def test_collect_candidates_includes_learned_when_model_ready(monkeypatch):
    monkeypatch.setenv("ML_PIPELINE_ENABLED", "true")
    monkeypatch.setenv("FACTOR_WEIGHTING_MODE", "hybrid")

    mock_learned = MagicMock()
    mock_learned.model = object()

    with patch(
        "backend.services.ml.activation_service.get_learned_weighting_singleton",
        return_value=mock_learned,
    ), patch(
        "backend.services.ml.activation_service.is_ml_activation_enabled",
        return_value=True,
    ), patch(
        "backend.services.promotion_scan_service._trade_metrics_from_paper",
        return_value=(25, 0.52, 0.08, [0.01] * 10),
    ):
        from backend.services.promotion_scan_service import collect_candidates

        db = MagicMock()
        cands = collect_candidates(db)
        ids = {c.candidate_id for c in cands}
        assert "ml_learned_weighting" in ids
        assert "factor_hybrid_mode" in ids


def test_run_promotion_scan_tick_starts_thread(monkeypatch):
    monkeypatch.setenv("PROMOTION_GATE_ENABLED", "true")
    started = []

    def fake_worker(sid, tick):
        started.append((sid, tick))

    monkeypatch.setattr(
        "backend.services.promotion_scan_service._scan_worker",
        fake_worker,
    )
    from backend.services.promotion_scan_service import run_promotion_scan_tick

    out = run_promotion_scan_tick("sess-1", 5, is_maintenance=True, force=True)
    assert out.get("started") is True
    import time
    time.sleep(0.3)
    assert started


def test_runtime_governor_applies_promotion_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMOTION_GATE_ENABLED", "true")
    reg = tmp_path / "reg.json"
    monkeypatch.setattr(
        "backend.services.promotion_scan_service.REGISTRY_PATH",
        str(reg),
    )
    from backend.services.runtime_governor import RuntimeGovernor

    gov = RuntimeGovernor()
    ok = gov._apply_promotion_gate_patch({
        "candidate_id": "ml_learned_weighting",
        "to_stage": "canary",
        "domain": "factor_weighting",
        "dsr": 0.7,
    })
    assert ok is True
    with open(reg, encoding="utf-8") as f:
        data = json.load(f)
    assert data["candidates"]["ml_learned_weighting"]["stage"] == "canary"


def test_factor_pipeline_respects_shadow_zero_blend(monkeypatch):
    monkeypatch.setenv("FACTOR_WEIGHTING_MODE", "hybrid")
    monkeypatch.setattr(
        "backend.services.promotion_scan_service.get_effective_learned_blend",
        lambda *_a, **_k: 0.0,
    )
    from backend.services.factor_engine.factor_evaluation_pipeline import FactorEvaluationPipeline
    import pandas as pd

    pipe = FactorEvaluationPipeline()

    class _Comp:
        direction = 0.5
        strength = 0.4
        confidence = 0.6

    mock_learned = MagicMock()
    mock_learned.model = object()
    mock_learned.feature_columns = ["f1"]
    mock_learned.predict_score.return_value = pd.Series([0.2])

    pipe._learned = mock_learned
    fv = MagicMock(normalized=0.1, value=0.1, has_data=True, is_directional=True)
    out = pipe._blend_learned_signal({"f1": fv}, {}, _Comp())
    assert out.direction == 0.5
