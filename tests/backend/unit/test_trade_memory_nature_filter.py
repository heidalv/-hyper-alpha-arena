"""trade_memory_context nature 分库过滤单测。"""
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace


pytestmark = pytest.mark.unit


def _trade(nature: str, symbol: str = "BTC", pnl: float = 10.0):
    return SimpleNamespace(
        symbol=symbol,
        side="long",
        pnl=pnl,
        pnl_pct=10.0,
        holding_period=3600,
        closed_at=datetime.now(timezone.utc),
        decision_context={"nature": nature},
        ai_reasoning="test",
    )


class TestTradeMemoryNatureFilter:
    def test_matches_nature_filter_swing(self):
        from backend.services.trade_memory_context import (
            _matches_nature_filter,
            _trade_nature_of,
        )

        t = _trade("swing")
        assert _trade_nature_of(t) == "swing"
        assert _matches_nature_filter(t, "swing") is True
        assert _matches_nature_filter(t, "trend") is False

    def test_matches_nature_filter_trend_group(self):
        from backend.services.trade_memory_context import _matches_nature_filter

        assert _matches_nature_filter(_trade("trend_follow"), "trend") is True
        assert _matches_nature_filter(_trade("position"), "trend") is True
        assert _matches_nature_filter(_trade("swing"), "trend") is False

    def test_build_section_uses_nature_label(self, db_session, monkeypatch):
        from backend.services import trade_memory_context as tmc

        def _fake_fetch(db, limit=50, *, window_hours=48, nature=None):
            trades = [
                _trade("swing", "BTC"),
                _trade("trend_follow", "ETH"),
            ]
            if nature:
                trades = [t for t in trades if tmc._matches_nature_filter(t, nature)]
            return trades[:limit]

        monkeypatch.setattr(tmc, "_fetch_recent_closed_trades", _fake_fetch)

        section = tmc.build_recent_trades_section(db_session, limit=10, nature="swing")
        assert "BTC" in section
        assert "ETH" not in section
        assert "仅中线(swing)" in section

        section_trend = tmc.build_recent_trades_section(db_session, limit=10, nature="trend")
        assert "ETH" in section_trend
        assert "BTC" not in section_trend
