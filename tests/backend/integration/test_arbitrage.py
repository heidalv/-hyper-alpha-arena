"""
套利系统集成测试

覆盖:
- FundingRateExecutor: 开仓/结算/平仓流程
- BasisArbExecutor: 基差检测/开仓/收敛平仓
- 风控拦截
"""

import pytest
import time


@pytest.mark.integration
class TestFundingRateExecutor:
    def test_execute_opportunity(self):
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
            recommended_size=5000, risk_score=0.3, confidence=0.8,
        )
        result = executor.execute_opportunity(opp)
        assert result.success
        assert result.position_id

    def test_reject_low_yield(self):
        from backend.services.arbitrage.funding_rate_executor import FundingRateExecutor
        from backend.services.arbitrage.models import ArbitrageOpportunity, FundingRateSnapshot

        executor = FundingRateExecutor()
        snap = FundingRateSnapshot(
            symbol="ETH", current_rate=0.0001, predicted_rate=0.0001,
            rate_8h_avg=0.0001, rate_24h_avg=0.0001,
            annual_yield=0.05, oi_total=5e8, volume_24h=2e8,
        )
        opp = ArbitrageOpportunity(
            opportunity_id="opp2", symbol="ETH", strategy="funding_long",
            expected_annual_yield=0.05, funding_snapshot=snap,
            recommended_size=3000, risk_score=0.5, confidence=0.5,
        )
        result = executor.execute_opportunity(opp)
        assert not result.success

    def test_no_duplicate_positions(self):
        from backend.services.arbitrage.funding_rate_executor import FundingRateExecutor
        from backend.services.arbitrage.models import ArbitrageOpportunity, FundingRateSnapshot

        executor = FundingRateExecutor()
        snap = FundingRateSnapshot(
            symbol="BTC", current_rate=0.001, predicted_rate=0.0008,
            rate_8h_avg=0.0009, rate_24h_avg=0.0008,
            annual_yield=0.35, oi_total=1e9, volume_24h=5e8,
        )
        opp = ArbitrageOpportunity(
            opportunity_id="opp3", symbol="BTC", strategy="funding_long",
            expected_annual_yield=0.35, funding_snapshot=snap,
            recommended_size=5000, risk_score=0.3, confidence=0.8,
        )
        r1 = executor.execute_opportunity(opp)
        r2 = executor.execute_opportunity(opp)
        assert r1.success
        assert not r2.success

    def test_monitor_positions(self):
        from backend.services.arbitrage.funding_rate_executor import FundingRateExecutor
        from backend.services.arbitrage.models import ArbitrageOpportunity, FundingRateSnapshot

        executor = FundingRateExecutor()
        snap = FundingRateSnapshot(
            symbol="SOL", current_rate=0.002, predicted_rate=0.0015,
            rate_8h_avg=0.0018, rate_24h_avg=0.0016,
            annual_yield=0.55, oi_total=1e8, volume_24h=5e7,
        )
        opp = ArbitrageOpportunity(
            opportunity_id="opp4", symbol="SOL", strategy="funding_short",
            expected_annual_yield=0.55, funding_snapshot=snap,
            recommended_size=2000, risk_score=0.2, confidence=0.9,
        )
        executor.execute_opportunity(opp)
        positions = executor.monitor_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "SOL"

    def test_performance_summary(self):
        from backend.services.arbitrage.funding_rate_executor import FundingRateExecutor
        executor = FundingRateExecutor()
        summary = executor.get_performance_summary()
        assert "total_positions" in summary
        assert "total_pnl" in summary


@pytest.mark.integration
class TestBasisArbExecutor:
    def test_no_open_on_small_basis(self):
        from backend.services.arbitrage.basis_arb_executor import BasisArbExecutor, BasisSnapshot

        executor = BasisArbExecutor()
        snap = BasisSnapshot(symbol="BTC", perp_price=50100, spot_price=50000, basis_pct=0.1)
        actions = executor.scan_and_execute([snap])
        # Basis 0.1% < threshold 0.3% → no open
        assert len([a for a in actions if a.get("action") == "open"]) == 0

    def test_open_on_large_basis(self):
        from backend.services.arbitrage.basis_arb_executor import BasisArbExecutor, BasisSnapshot

        executor = BasisArbExecutor()
        # Need 3 consecutive snapshots for stable direction
        for _ in range(3):
            snap = BasisSnapshot(symbol="ETH", perp_price=3050, spot_price=3000, basis_pct=0.5)
            executor.record_basis(snap)

        snap = BasisSnapshot(symbol="ETH", perp_price=3050, spot_price=3000, basis_pct=0.5)
        actions = executor.scan_and_execute([snap])
        opens = [a for a in actions if a.get("action") == "open"]
        assert len(opens) == 1
        assert opens[0]["direction"] == "short_perp"
