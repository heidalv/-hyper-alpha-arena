import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)


class FakeReport:
    def __init__(self, total_closed=10, win_rate=0.5, total_pnl=100.0):
        self.total_closed = total_closed
        self.win_rate = win_rate
        self.total_pnl = total_pnl

    def to_dict(self):
        return {"total_closed": self.total_closed, "win_rate": self.win_rate, "total_pnl": self.total_pnl}


def make_mock_db(query_rows=None):
    db = MagicMock()
    rows = query_rows or []
    db.query.return_value.filter.return_value.all.return_value = rows
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    return db


@pytest.fixture
def patch_eval_env():
    patches = [
        patch("backend.database.connection.sqlite_write_commit"),
        patch("backend.config.settings.OPENCODE_VALIDATION_HOURS", 24),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


def test_fix1_zero_samples_no_rollback(patch_eval_env):
    from backend.services.opencode_proposal_applier import evaluate_applied_proposals
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = MagicMock()
    row.id = 1
    row.status = "paper_applying"
    row.applied_at = now - timedelta(hours=30)
    row.baseline_json = json.dumps({"baseline_perf": {"win_rate": 0.5}})
    row.after_json = "{}"
    row.validated_at = None
    db = make_mock_db(query_rows=[row])
    fake_report = FakeReport(total_closed=0, win_rate=0.0, total_pnl=0.0)
    with patch("backend.services.strategy_runtime_report.generate_report", return_value=fake_report):
        n = evaluate_applied_proposals(db)
    assert row.status == "paper_applying"
    assert n == 0


def test_fix1_low_samples_no_rollback(patch_eval_env):
    from backend.services.opencode_proposal_applier import evaluate_applied_proposals
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = MagicMock()
    row.id = 2
    row.status = "paper_applying"
    row.applied_at = now - timedelta(hours=30)
    row.baseline_json = json.dumps({"baseline_perf": {"win_rate": 0.5}})
    row.after_json = "{}"
    row.validated_at = None
    db = make_mock_db(query_rows=[row])
    fake_report = FakeReport(total_closed=3, win_rate=0.0, total_pnl=-10.0)
    with patch("backend.services.strategy_runtime_report.generate_report", return_value=fake_report):
        n = evaluate_applied_proposals(db)
    assert row.status == "paper_applying"
    assert n == 0


def test_fix1_sufficient_degraded_rolls_back(patch_eval_env):
    from backend.services.opencode_proposal_applier import evaluate_applied_proposals
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = MagicMock()
    row.id = 3
    row.status = "paper_applying"
    row.applied_at = now - timedelta(hours=30)
    row.baseline_json = json.dumps({"baseline_perf": {"win_rate": 0.5}})
    row.after_json = "{}"
    row.validated_at = None
    db = make_mock_db(query_rows=[row])
    fake_report = FakeReport(total_closed=10, win_rate=0.30, total_pnl=-50.0)
    with patch("backend.services.strategy_runtime_report.generate_report", return_value=fake_report):
        with patch("backend.services.runtime_tuning_store.rollback_snapshot", return_value=True):
            with patch("backend.services.decision_policy_engine.rollback_policy_snapshot", return_value=0):
                n = evaluate_applied_proposals(db)
    assert row.status == "rolled_back"
    assert n == 1


def test_fix1_sufficient_not_degraded_validates(patch_eval_env):
    from backend.services.opencode_proposal_applier import evaluate_applied_proposals
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = MagicMock()
    row.id = 4
    row.status = "paper_applying"
    row.applied_at = now - timedelta(hours=30)
    row.baseline_json = json.dumps({"baseline_perf": {"win_rate": 0.5}})
    row.after_json = "{}"
    row.validated_at = None
    db = make_mock_db(query_rows=[row])
    fake_report = FakeReport(total_closed=10, win_rate=0.55, total_pnl=200.0)
    with patch("backend.services.strategy_runtime_report.generate_report", return_value=fake_report):
        n = evaluate_applied_proposals(db)
    assert row.status == "paper_validated"
    assert n == 1


def test_fix1_timeout_inconclusive(patch_eval_env):
    from backend.services.opencode_proposal_applier import evaluate_applied_proposals
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = MagicMock()
    row.id = 5
    row.status = "paper_applying"
    row.applied_at = now - timedelta(hours=100)
    row.baseline_json = json.dumps({"baseline_perf": {"win_rate": 0.5}})
    row.after_json = "{}"
    row.validated_at = None
    db = make_mock_db(query_rows=[row])
    fake_report = FakeReport(total_closed=2, win_rate=0.0, total_pnl=0.0)
    with patch("backend.services.strategy_runtime_report.generate_report", return_value=fake_report):
        n = evaluate_applied_proposals(db)
    assert row.status == "inconclusive"
    assert n == 1


def test_fix2_dedupe_rejected_not_recreated():
    from backend.services.opencode_proposal_applier import create_proposal
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing = MagicMock()
    existing.id = 99
    existing.status = "rejected"
    existing.created_at = now - timedelta(days=1)
    existing.proposal_json = json.dumps({"patches": [], "dedupe_key": "abc123"})
    db = make_mock_db(query_rows=[existing])
    patches = [{"key": "max_daily_trades", "value": 8, "type": "tuning"}]
    with patch("backend.database.connection.sqlite_write_commit"):
        result = create_proposal(db, patches, severity="minor", title="test", dedupe_key="abc123")
    assert result == 99
    db.add.assert_not_called()


def test_fix2_dedupe_rolled_back_not_recreated():
    from backend.services.opencode_proposal_applier import create_proposal
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing = MagicMock()
    existing.id = 88
    existing.status = "rolled_back"
    existing.created_at = now - timedelta(days=3)
    existing.proposal_json = json.dumps({"patches": [], "dedupe_key": "xyz789"})
    db = make_mock_db(query_rows=[existing])
    patches = [{"key": "max_daily_trades", "value": 10, "type": "tuning"}]
    with patch("backend.database.connection.sqlite_write_commit"):
        result = create_proposal(db, patches, severity="minor", title="test", dedupe_key="xyz789")
    assert result == 88
    db.add.assert_not_called()


def test_fix2_different_dedupe_creates_new():
    from backend.services.opencode_proposal_applier import create_proposal
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing = MagicMock()
    existing.id = 55
    existing.status = "rejected"
    existing.created_at = now - timedelta(days=1)
    existing.proposal_json = json.dumps({"patches": [], "dedupe_key": "different"})
    db = make_mock_db(query_rows=[existing])
    patches = [{"key": "max_daily_trades", "value": 8, "type": "tuning"}]
    with patch("backend.database.connection.sqlite_write_commit"):
        result = create_proposal(db, patches, severity="minor", title="new", dedupe_key="brand_new")
    db.add.assert_called_once()


def test_fix3_deprecated_in_config():
    routes_path = os.path.join(PROJECT_ROOT, "backend", "api", "opencode_routes.py")
    with open(routes_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "OPENCODE_AUTO_APPLY_MINOR_DEPRECATED" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
