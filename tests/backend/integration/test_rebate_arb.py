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

    def test_deprecated_strategies_absent_from_yaml(self):
        """R3 死代码清除：S1/S5/S6 已从 YAML 移除。"""
        from backend.config.rebate_config_loader import load_config

        cfg = load_config()
        for key in ("S1_maker_hedge", "S5_funding_points", "S6_cross_fee_spread"):
            assert cfg.strategies.get(key) is None


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
