"""#9 Phase4 写退役 + event-first 回归。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_es(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "true")
    monkeypatch.setenv("EVENT_SOURCING_LOG_PATH", str(tmp_path / "es_p4.jsonl"))
    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "true")
    monkeypatch.setenv("EVENT_SOURCING_WRITE_RETIRE_DB", "true")
    from backend.services.event_sourcing.phase2 import reset_live_repository_for_tests
    reset_live_repository_for_tests()
    yield
    reset_live_repository_for_tests()


def test_write_retirement_requires_phase3(monkeypatch):
    from backend.services.event_sourcing.phase4 import is_write_retirement_enabled

    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "false")
    assert is_write_retirement_enabled() is False
    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "true")
    assert is_write_retirement_enabled() is True


def test_record_event_first_increments_stats():
    from backend.services.event_sourcing.phase4 import get_phase4_stats, record_event_first

    before = get_phase4_stats().get("event_first_writes", 0)
    ok = record_event_first(
        "PositionOpened", "99",
        {"account_id": 1, "symbol": "ETH", "side": "long", "size": 1.0, "entry_price": 3000},
    )
    assert ok is True
    assert get_phase4_stats().get("event_first_writes", 0) >= before + 1


def test_run_retirement_sync_skipped_when_reconcile_bad(monkeypatch):
    from backend.services.event_sourcing.phase2 import get_live_repository, reset_live_repository_for_tests
    from backend.services.event_sourcing.phase4 import record_event_first, run_retirement_sync

    reset_live_repository_for_tests()
    record_event_first(
        "PositionOpened", "7",
        {"account_id": 2, "symbol": "BTC", "side": "long", "size": 0.5, "entry_price": 60000},
    )

    class _BadRepo:
        projection = type("P", (), {"current_state": {}, "open_positions": lambda self: []})()

    monkeypatch.setattr(
        "backend.services.event_sourcing.phase4.get_live_repository",
        lambda: _BadRepo(),
    )
    monkeypatch.setattr(
        "backend.services.event_sourcing.phase4.is_phase2_reconcile_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "backend.services.event_sourcing.phase4.get_reconcile_stats",
        lambda: {"last_ok": 0},
    )

    db = type("DB", (), {"commit": lambda self: None, "rollback": lambda self: None})()
    out = run_retirement_sync(db)
    assert out.get("skipped", 0) >= 1
