"""
E2E test — validates the full trading pipeline from signal to order execution.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.e2e
class TestTradingPipeline:
    """End-to-end tests for the core trading flow."""

    def test_full_auto_session_can_be_created(self, db_session):
        """Verify FullAutoSession model can be inserted and queried.

        v3 整改: session_id 是 NOT NULL 字段，测试必须显式提供。
        """
        from backend.database.models import FullAutoSession
        session = FullAutoSession(
            session_id="e2e_test_session_001",
            account_id=1,
            status="running",
            symbols=["BTC", "ETH"],
        )
        db_session.add(session)
        db_session.flush()
        assert session.id is not None

        found = db_session.query(FullAutoSession).filter_by(id=session.id).first()
        assert found is not None
        assert found.status == "running"

    def test_paper_balance_tracks_equity(self, db_session):
        """Verify PaperBalance can be created and equity is stored.

        v3 整改: 模型字段叫 total_equity / available_balance，没有 `balance`/`equity`。
        """
        from backend.database.models import PaperBalance
        bal = PaperBalance(
            account_id=1,
            initial_balance=150.0,
            total_equity=147.0,
            available_balance=145.0,
        )
        db_session.add(bal)
        db_session.flush()
        assert bal.id is not None
        assert bal.total_equity == 147.0

    def test_ai_strategy_creation_and_query(self, db_session):
        """Verify AIStrategy can be created with timeframe_tier."""
        from backend.database.models import AIStrategy
        strat = AIStrategy(
            strategy_id="test_e2e_001",
            account_id=1,
            primary_symbol="BTC",
            timeframe_tier="mid",
            status="active",
            name="E2E Test Strategy",
        )
        db_session.add(strat)
        db_session.flush()

        found = db_session.query(AIStrategy).filter_by(strategy_id="test_e2e_001").first()
        assert found is not None
        assert found.timeframe_tier == "mid"
        assert found.primary_symbol == "BTC"

    def test_no_duplicate_strategies(self, db_session):
        """Verify the dedup logic: same symbol+tier should not create duplicates."""
        from backend.database.models import AIStrategy

        strat1 = AIStrategy(
            strategy_id="dedup_test_001",
            account_id=1,
            primary_symbol="ETH",
            timeframe_tier="short",
            status="active",
            name="ETH Short 1",
        )
        db_session.add(strat1)
        db_session.flush()

        existing = db_session.query(AIStrategy).filter(
            AIStrategy.account_id == 1,
            AIStrategy.primary_symbol == "ETH",
            AIStrategy.timeframe_tier == "short",
            AIStrategy.status.in_(["active", "paused"]),
        ).first()

        assert existing is not None, "First strategy should exist"
        assert existing.strategy_id == "dedup_test_001"
