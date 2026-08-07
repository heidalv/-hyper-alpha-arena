"""Hermes Agent 智慧采集单测。"""
import pytest


pytestmark = pytest.mark.unit


class TestHermesAgentWisdom:
    def test_extract_swing_outcome(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        import backend.services.hermes_db as hdb

        db_path = tmp_path / "hermes_test.db"
        monkeypatch.setattr(hdb, "HERMES_DB_PATH", str(db_path))
        hdb.init_hermes_db()

        from backend.services.hermes_agent_wisdom_engine import agent_wisdom

        outcome = SimpleNamespace(
            trade_nature="swing",
            symbol="BTC",
            side="long",
            pnl=120.0,
            pnl_pct=2.4,
            confidence=0.65,
            regime_at_entry="trending",
            regime_at_exit="trending",
            duration_seconds=7200,
            fingerprint_at_entry={"funding_rate": 0.0001},
            metadata={"paper_position_id": 99, "close_reason": "tp"},
        )
        assert agent_wisdom.extract_wisdom_from_outcome(outcome) is True

        rows = hdb.hermes_fetchall(
            "SELECT agent_type, outcome, pattern_key FROM agent_decision_wisdom",
            (),
        )
        assert len(rows) == 1
        assert rows[0]["agent_type"] == "swing"
        assert rows[0]["outcome"] == "win"

    def test_intraday_maps_to_swing(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        import backend.services.hermes_db as hdb

        db_path = tmp_path / "hermes_intraday.db"
        monkeypatch.setattr(hdb, "HERMES_DB_PATH", str(db_path))
        hdb.init_hermes_db()

        from backend.services.hermes_agent_wisdom_engine import agent_wisdom

        outcome = SimpleNamespace(
            trade_nature="intraday",
            symbol="ETH",
            side="long",
            pnl=-10,
            pnl_pct=-1.0,
            confidence=0.5,
            regime_at_entry="ranging",
            regime_at_exit="ranging",
            duration_seconds=3600,
            fingerprint_at_entry={},
            metadata={"close_reason": "sl"},
        )
        assert agent_wisdom.extract_wisdom_from_outcome(outcome) is True
        rows = hdb.hermes_fetchall(
            "SELECT agent_type, outcome FROM agent_decision_wisdom", ()
        )
        assert rows[0]["agent_type"] == "swing"
        assert rows[0]["outcome"] == "loss"
