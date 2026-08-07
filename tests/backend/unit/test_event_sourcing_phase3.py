"""
#9 事件溯源 Phase 3 回归：投影默认读、DB 引导、统一读路径。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_es(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_SOURCING_ENABLED", "true")
    monkeypatch.setenv("EVENT_SOURCING_LOG_PATH", str(tmp_path / "es_phase3.jsonl"))
    monkeypatch.setenv("EVENT_SOURCING_PHASE2_RECONCILE", "true")
    monkeypatch.setenv("EVENT_SOURCING_PHASE2_READ", "false")
    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "true")
    from backend.services.event_sourcing.phase2 import reset_live_repository_for_tests
    reset_live_repository_for_tests()
    yield
    reset_live_repository_for_tests()


def test_phase3_projection_read_active_without_phase2_read(monkeypatch):
    from backend.services.event_sourcing.phase3 import is_projection_read_active

    monkeypatch.setenv("EVENT_SOURCING_PHASE2_READ", "false")
    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "true")
    assert is_projection_read_active() is True


def test_phase3_bootstrap_writes_missing_position():
    from backend.services.event_sourcing.phase3 import bootstrap_db_position_row
    from backend.services.event_sourcing.phase2 import get_live_repository

    ok = bootstrap_db_position_row(
        {"id": 88, "symbol": "BTC", "side": "long", "size": 1.5, "entry_price": 50000},
        account_id=1,
    )
    assert ok is True
    state = get_live_repository().projection.current_state["88"]
    assert state["symbol"] == "BTC"
    assert bootstrap_db_position_row(
        {"id": 88, "symbol": "BTC", "side": "long", "size": 1.5},
        account_id=1,
    ) is False


def test_phase3_resolve_read_uses_projection_when_reconcile_ok():
    from backend.services.event_sourcing.phase3 import resolve_position_list_for_read
    from backend.services.event_sourcing import record_position_event, EVT_POSITION_OPENED

    record_position_event(
        EVT_POSITION_OPENED, "12",
        {"account_id": 5, "symbol": "ETH", "side": "long", "size": 2.0, "entry_price": 3000},
    )
    db_rows = [{"id": 12, "status": "open", "symbol": "ETH", "side": "long", "size": 2.0, "mark_price": 3100}]
    out = resolve_position_list_for_read(db_rows, account_id=5, status="open")
    assert len(out) == 1
    assert out[0].get("_source") == "event_projection"
    assert out[0].get("mark_price") == 3100


def test_phase3_resolve_falls_back_to_db_on_mismatch():
    from backend.services.event_sourcing.phase3 import resolve_position_list_for_read

    db_rows = [{"id": 77, "status": "open", "symbol": "SOL", "side": "long", "size": 1.0}]
    out = resolve_position_list_for_read(db_rows, account_id=9, status="open")
    assert len(out) == 1
    assert out[0].get("_source") is None


def test_phase3_disabled_skips_bootstrap(monkeypatch):
    from backend.services.event_sourcing.phase3 import bootstrap_db_position_row

    monkeypatch.setenv("EVENT_SOURCING_PHASE3", "false")
    assert bootstrap_db_position_row({"id": 1, "symbol": "X"}, account_id=1) is False
