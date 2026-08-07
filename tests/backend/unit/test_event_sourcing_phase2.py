"""
#9 事件溯源 Phase 2 回归测试：双写、投影读、C7 对拍。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_es_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "true")
    monkeypatch.setenv("EVENT_SOURCING_LOG_PATH", str(tmp_path / "es.jsonl"))
    monkeypatch.setenv("EVENT_SOURCING_PHASE2_RECONCILE", "true")
    monkeypatch.setenv("EVENT_SOURCING_PHASE2_READ", "false")
    from backend.services.event_sourcing.phase2 import reset_live_repository_for_tests
    reset_live_repository_for_tests()
    yield
    reset_live_repository_for_tests()


def test_phase2_record_and_apply_updates_projection():
    from backend.services.event_sourcing.phase2 import (
        get_live_repository,
        record_position_event,
    )
    from backend.services.event_sourcing import EVT_POSITION_OPENED

    ok = record_position_event(
        EVT_POSITION_OPENED, "101",
        {"account_id": 1, "symbol": "BTC", "side": "long", "size": 1.0, "entry_price": 60000},
    )
    assert ok is True
    repo = get_live_repository()
    state = repo.projection.current_state["101"]
    assert state["status"] == "open"
    assert state["size"] == pytest.approx(1.0)
    assert state["account_id"] == 1


def test_phase2_reconcile_ok_when_db_matches():
    from backend.services.event_sourcing.phase2 import (
        record_position_event,
        reconcile_db_vs_projection,
    )
    from backend.services.event_sourcing import EVT_POSITION_OPENED

    record_position_event(
        EVT_POSITION_OPENED, "55",
        {"account_id": 2, "symbol": "ETH", "side": "short", "size": 3.0, "entry_price": 3000},
    )
    db_rows = [{
        "id": 55, "status": "open", "symbol": "ETH", "side": "short", "size": 3.0,
    }]
    rec = reconcile_db_vs_projection(db_rows, account_id=2)
    assert rec.ok is True


def test_phase2_reconcile_detects_missing_in_projection():
    from backend.services.event_sourcing.phase2 import reconcile_db_vs_projection

    db_rows = [{"id": 99, "status": "open", "symbol": "BTC", "side": "long", "size": 1.0}]
    rec = reconcile_db_vs_projection(db_rows, account_id=1)
    assert rec.ok is False
    assert "99" in rec.missing_in_proj


def test_phase2_read_uses_projection_when_reconcile_ok(monkeypatch):
    from backend.services.event_sourcing.phase2 import (
        record_position_event,
        projection_positions_for_account,
    )
    from backend.services.event_sourcing import EVT_POSITION_OPENED

    monkeypatch.setenv("EVENT_SOURCING_PHASE2_READ", "true")
    record_position_event(
        EVT_POSITION_OPENED, "7",
        {"account_id": 3, "symbol": "SOL", "side": "long", "size": 2.0, "entry_price": 150},
    )
    rows = projection_positions_for_account(3, status="open")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SOL"
    assert rows[0].get("_source") == "event_projection"


def test_phase2_disabled_is_noop(monkeypatch, tmp_path):
    from backend.services.event_sourcing.phase2 import record_position_event, get_live_repository
    from backend.services.event_sourcing import EVT_POSITION_OPENED

    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "false")
    reset = __import__(
        "backend.services.event_sourcing.phase2", fromlist=["reset_live_repository_for_tests"]
    ).reset_live_repository_for_tests
    reset()
    assert record_position_event(EVT_POSITION_OPENED, "1", {"size": 1}) is False
    assert get_live_repository().projection.open_positions() == {}
