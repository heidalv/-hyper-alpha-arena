"""
Unit tests for MultiTimeframeOrchestrator — validates signal merging and direction logic.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestOrchestratorParamLoading:
    """Tests for parameter loading from evolver."""

    def test_load_params_updates_internal_state(self):
        from backend.services.multi_timeframe_orchestrator import MultiTimeframeOrchestrator
        orch = MultiTimeframeOrchestrator()
        original = dict(orch._params)
        orch.load_params({"mid_rsi_bull": 60})
        assert orch._params["mid_rsi_bull"] == 60
        # Restore
        orch.load_params(original)

    def test_load_params_ignores_unknown_keys(self):
        from backend.services.multi_timeframe_orchestrator import MultiTimeframeOrchestrator
        orch = MultiTimeframeOrchestrator()
        before = dict(orch._params)
        orch.load_params({"unknown_param_xyz": 999})
        assert orch._params == before


class TestOrchestratorTradeNature:
    """Tests for trade nature inference."""

    def test_infer_trade_nature_returns_valid(self):
        """v3 整改: _infer_trade_nature 现在接收 OrchestratorDecision 而非单独的 signal 字典。
        构造三周期都 bullish 且有足够置信的 decision，验证返回值属于五档合法集合。"""
        from backend.services.multi_timeframe_orchestrator import (
            MultiTimeframeOrchestrator,
            OrchestratorDecision,
            TimeframeView,
        )
        orch = MultiTimeframeOrchestrator()
        valid_natures = {"trend_follow", "swing", "intraday", "scalp", "position"}
        decision = OrchestratorDecision(symbol="BTC")
        decision.long_view = TimeframeView("long", bias="bullish", confidence=0.7)
        decision.mid_view = TimeframeView("mid", bias="bullish", confidence=0.6)
        decision.short_view = TimeframeView("short", bias="bullish", confidence=0.5)
        nature = orch._infer_trade_nature(decision)
        assert nature in valid_natures
