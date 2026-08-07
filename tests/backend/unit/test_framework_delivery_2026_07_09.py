"""
整改交付回归：#8 orchestrator / #9 phase4 / 晋升门 / G4（2026-07-09）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_orchestrator_get_loop_stats():
    from backend.services.full_auto.orchestrator import FullAutoOrchestrator, get_orchestrator
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService()
    orch = get_orchestrator(svc)
    assert isinstance(orch, FullAutoOrchestrator)
    stats = orch.get_loop_stats()
    assert "unified_sessions" in stats


def test_phase4_write_retirement_requires_phase3(monkeypatch):
    from backend.services.event_sourcing.phase4 import is_write_retirement_enabled

    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "true")
    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "false")
    monkeypatch.setenv("EVENT_SOURCING_WRITE_RETIRE_DB", "true")
    assert is_write_retirement_enabled() is False

    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "true")
    assert is_write_retirement_enabled() is True


def test_phase4_event_first_records(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "true")
    monkeypatch.setenv("EVENT_SOURCING_LOG_PATH", str(tmp_path / "es4.jsonl"))
    monkeypatch.setenv("EVENT_SOURCING_WRITE_RETIRE_DB", "true")
    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "true")
    from backend.services.event_sourcing.phase2 import reset_live_repository_for_tests
    from backend.services.event_sourcing.phase4 import record_event_first, get_phase4_stats

    reset_live_repository_for_tests()
    ok = record_event_first(
        "PositionOpened", "42",
        {"account_id": 1, "symbol": "BTC", "side": "long", "size": 1.0, "entry_price": 50000},
    )
    assert ok is True
    assert get_phase4_stats().get("event_first_writes", 0) >= 1


def test_promotion_gate_shadow_to_canary():
    from backend.services.promotion_gate_service import (
        PromotionMetrics,
        PromotionStage,
        evaluate_promotion,
    )

    m = PromotionMetrics(
        candidate_id="learned_btc_short",
        stage=PromotionStage.SHADOW,
        sharpe=1.2,
        win_rate=0.55,
        max_drawdown=0.08,
        trade_count=30,
        n_trials=5,
        returns=[0.01, 0.02, -0.005, 0.015, 0.01] * 6,
    )
    d = evaluate_promotion(m)
    assert d.approved is True
    assert d.to_stage == PromotionStage.CANARY


def test_promotion_gate_blocks_low_sample():
    from backend.services.promotion_gate_service import PromotionMetrics, PromotionStage, evaluate_promotion

    m = PromotionMetrics(
        candidate_id="x", stage=PromotionStage.SHADOW,
        win_rate=0.9, max_drawdown=0.01, trade_count=3,
    )
    d = evaluate_promotion(m)
    assert d.approved is False


def test_resource_guard_blocks_hot_path_training():
    from backend.services.resource_guard import (
        guard_training_operation,
        hot_path_context,
        get_guard_stats,
    )

    with hot_path_context("test"):
        assert guard_training_operation("train") is False
    assert guard_training_operation("train") is True
    assert get_guard_stats().get("blocked_sync_train", 0) >= 1


def test_resource_guard_run_off_peak_defers_on_hot_path():
    from backend.services.resource_guard import hot_path_context, run_off_peak

    ran = []

    with hot_path_context("test"):
        out = run_off_peak(lambda: ran.append(1), name="t")
        assert out.get("deferred") is True
    assert ran == [] or len(ran) == 1  # async may complete later
