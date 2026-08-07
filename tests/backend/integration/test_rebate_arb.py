"""
Rebate 套利基础测试
"""

import pytest


@pytest.mark.integration
class TestRebateConfig:
    def test_auto_execute_default_off(self):
        from backend.config.rebate_config_loader import load_config

        cfg = load_config()
        assert cfg.engine.auto_execute is False

    def test_s1_disabled_in_yaml(self):
        from backend.config.rebate_config_loader import load_config

        cfg = load_config()
        s1 = cfg.strategies.get("S1_maker_hedge")
        assert s1 is not None
        assert s1.enabled is False


@pytest.mark.integration
class TestS1MakerHedge:
    def test_not_viable_when_negative_ev(self):
        from backend.services.rebate_arb.strategies.s1_maker_hedge import S1MakerHedgeStrategy

        strat = S1MakerHedgeStrategy()
        ev = strat.evaluate(
            incentive_data={
                "asterdex": {"rebate_rate": 0.10, "maker_rate": 0.00005},
                "binance": {"taker_rate": 0.0004},
            },
            account_equity=300,
        )
        assert not ev.is_viable
        assert ev.details.get("net_monthly", 0) < 0


@pytest.mark.integration
class TestRebateEngineScan:
    def test_scan_all_strategies_returns_list(self):
        from backend.services.rebate_arb.engine import rebate_arb_engine

        results = rebate_arb_engine.scan_all_strategies(
            incentive_data={},
            funding_rates={},
            account_equity=300,
        )
        assert isinstance(results, list)
        assert len(results) >= 1


@pytest.mark.integration
class TestGlobalCapitalWithRebate:
    def test_rebate_pool_separate_from_v3(self):
        from backend.services.arbitrage.global_capital_coordinator import (
            GlobalCapitalCoordinator,
        )

        coord = GlobalCapitalCoordinator()
        coord.update_equity(1000.0)
        v3 = coord.get_v3_pool_available()
        rebate = coord.get_pool_available("rebate_points_arb")
        assert v3 > 0
        assert rebate > 0
        assert v3 + rebate <= 1000.0
