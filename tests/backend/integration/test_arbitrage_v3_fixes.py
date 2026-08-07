"""
套利系统扩展测试 — orchestrator / LiveExecutor / yield / global capital
"""

import pytest
import time
from unittest.mock import MagicMock, patch


@pytest.mark.integration
class TestFundingRateExecutorNotional:
    def test_execute_with_explicit_size_usd(self):
        from backend.services.arbitrage.funding_rate_executor import FundingRateExecutor
        from backend.services.arbitrage.models import ArbitrageOpportunity, FundingRateSnapshot

        executor = FundingRateExecutor()
        snap = FundingRateSnapshot(
            symbol="BTC", current_rate=0.001, predicted_rate=0.0008,
            rate_8h_avg=0.0009, rate_24h_avg=0.0008,
            annual_yield=0.35, oi_total=1e9, volume_24h=5e8,
        )
        opp = ArbitrageOpportunity(
            opportunity_id="opp1", symbol="BTC", strategy="funding_long",
            expected_annual_yield=0.35, funding_snapshot=snap,
            recommended_size=0.0, risk_score=0.3, confidence=0.8,
        )
        result = executor.execute_opportunity(opp, size_usd=2500.0, entry_price=50000.0)
        assert result.success
        positions = executor.monitor_positions()
        assert positions[0]["size_usd"] == 2500.0


@pytest.mark.integration
class TestYieldMetrics:
    def test_cross_exchange_score_not_overannualized(self):
        from backend.services.arbitrage.yield_metrics import cross_exchange_score

        score = cross_exchange_score(0.5, 2.0)
        assert score < 0.01  # 0.5% spread should not become 182% annual

    def test_funding_annual_yield(self):
        from backend.services.arbitrage.yield_metrics import funding_annual_yield

        assert funding_annual_yield(0.0001) == pytest.approx(0.0001 * 3 * 365, rel=1e-6)

    def test_normalize_score_funding(self):
        from backend.services.arbitrage.yield_metrics import normalize_score_for_sort

        s = normalize_score_for_sort("funding_rate", {"expected_annual_yield": 0.25})
        assert s == 0.25


@pytest.mark.integration
class TestGlobalCapitalCoordinator:
    def test_request_and_release(self):
        from backend.services.arbitrage.global_capital_coordinator import (
            GlobalCapitalCoordinator,
        )

        coord = GlobalCapitalCoordinator()
        coord.update_equity(1000.0)
        r = coord.request("funding_rate_arb", 50.0, "test")
        assert r["granted"]
        assert coord.get_pool_available("funding_rate_arb") < 100.0
        coord.release("funding_rate_arb", 50.0, "test")
        assert coord.get_pool_available("funding_rate_arb") == pytest.approx(100.0, rel=0.01)

    def test_v3_pool_available(self):
        from backend.services.arbitrage.global_capital_coordinator import (
            GlobalCapitalCoordinator,
        )

        coord = GlobalCapitalCoordinator()
        coord.update_equity(1000.0)
        v3 = coord.get_v3_pool_available()
        assert v3 > 0


@pytest.mark.integration
class TestLiveExecutorFunding:
    def test_paper_funding_single_direction(self):
        from backend.services.arbitrage.live_executor import LiveExecutor

        ex = LiveExecutor(mode="paper")
        result = ex.execute_funding({
            "exchange": "hyperliquid",
            "hedge_exchange": "binance",
            "symbol": "BTC",
            "size_usd": 500,
            "direction": "short",
            "entry_price": 50000,
        })
        assert result["ok"]
        assert result["mode"] == "paper"
        positions = ex.get_paper_positions()
        assert len(positions) == 1
        assert positions[0]["direction"] == "short"
        assert positions[0].get("hedge_exchange") == "binance"


@pytest.mark.integration
class TestOrchestratorRegisterPosition:
    def test_register_short_only(self):
        from backend.services.arbitrage.orchestrator import ArbitrageOrchestrator
        from backend.services.arbitrage.unified_models import StrategyType

        orch = ArbitrageOrchestrator()
        with patch("backend.services.arbitrage.orchestrator.save_position_open"):
            orch._register_position(
                "test_pos", "BTC", StrategyType.FUNDING_RATE,
                1000, 50000, "short",
                exchange_long="binance", exchange_short="hyperliquid",
            )
        pos = orch.active_positions["test_pos"]
        assert pos.short_size > 0
        assert pos.long_size == 0

    def test_register_long_only(self):
        from backend.services.arbitrage.orchestrator import ArbitrageOrchestrator
        from backend.services.arbitrage.unified_models import StrategyType

        orch = ArbitrageOrchestrator()
        with patch("backend.services.arbitrage.orchestrator.save_position_open"):
            orch._register_position(
                "test_pos2", "ETH", StrategyType.FUNDING_RATE,
                500, 3000, "long",
            )
        pos = orch.active_positions["test_pos2"]
        assert pos.long_size > 0
        assert pos.short_size == 0


@pytest.mark.integration
class TestArbConfigLoader:
    def test_load_config(self):
        from backend.config.arb_config_loader import load_config

        cfg = load_config()
        assert cfg.engine.max_pool_pct_of_equity > 0
        assert cfg.scanner.basis_scan_enabled is False
        assert cfg.funding.hedge_exchange == "binance"
