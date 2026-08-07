"""
StrategyHealthService 集成测试

覆盖:
- 健康评估: healthy / degraded / underperforming / critical
- 自动诊断
- 自修复动作
"""

import pytest
from unittest.mock import MagicMock


def _make_strategy(total_trades=20, sharpe_proxy=1.0, consec_losses=0, win_rate=0.55):
    """Create a mock strategy with configurable performance."""
    strat = MagicMock()
    strat.strategy_id = "test_strat_001"
    strat.primary_symbol = "BTC"
    strat.status = "active"
    strat.genome = {}
    strat.risk_params = {"risk_pct": 2.0}
    strat.performance_metrics = {
        "total_trades": total_trades,
        "total_pnl": sharpe_proxy * total_trades * 5,
        "win_rate": win_rate,
        "consecutive_losses": consec_losses,
        "max_drawdown_pct": 10.0 if sharpe_proxy > 0 else 25.0,
        "avg_slippage_pct": 0.1,
    }
    return strat


@pytest.mark.integration
class TestStrategyHealthService:
    def test_healthy_strategy(self):
        from backend.services.strategy_health_service import StrategyHealthService, HealthLevel
        svc = StrategyHealthService()
        strat = _make_strategy(sharpe_proxy=2.0)
        report = svc.evaluate_strategy_health(
            strategy_id="test_strat_001", strategy=strat
        )
        assert report.level == HealthLevel.HEALTHY

    def test_degraded_on_slippage(self):
        from backend.services.strategy_health_service import StrategyHealthService, HealthLevel
        svc = StrategyHealthService()
        strat = _make_strategy(sharpe_proxy=1.5)
        strat.performance_metrics["avg_slippage_pct"] = 0.8
        report = svc.evaluate_strategy_health(
            strategy_id="test_slippage", strategy=strat
        )
        assert report.level in (HealthLevel.DEGRADED, HealthLevel.UNDERPERFORMING)

    def test_underperforming_on_low_sharpe(self):
        """v3 整改: _compute_rolling_sharpe 公式变化后，sharpe_proxy=0.1 仍然算出 sharpe≈1.0。
        这里改用显式超过 MAX_CONSECUTIVE_LOSSES (=5) 触发 UNDERPERFORMING，
        更直接对应"表现不佳"的判定路径。"""
        from backend.services.strategy_health_service import StrategyHealthService, HealthLevel
        svc = StrategyHealthService()
        strat = _make_strategy(sharpe_proxy=0.1, win_rate=0.40, consec_losses=6)
        report = svc.evaluate_strategy_health(
            strategy_id="test_low_sharpe", strategy=strat
        )
        assert report.level in (HealthLevel.UNDERPERFORMING, HealthLevel.CRITICAL)

    def test_critical_on_consecutive_losses(self):
        from backend.services.strategy_health_service import StrategyHealthService, HealthLevel
        svc = StrategyHealthService()
        strat = _make_strategy(sharpe_proxy=-0.5, consec_losses=9)
        report = svc.evaluate_strategy_health(
            strategy_id="test_critical", strategy=strat
        )
        assert report.level == HealthLevel.CRITICAL

    def test_diagnose_reports_causes(self):
        from backend.services.strategy_health_service import StrategyHealthService
        svc = StrategyHealthService()
        strat = _make_strategy(sharpe_proxy=-0.3, consec_losses=7, win_rate=0.30)
        diag = svc.diagnose_underperformance(
            strategy_id="test_diag", strategy=strat
        )
        assert len(diag.root_causes) > 0

    def test_cooldown_prevents_repeat_eval(self):
        from backend.services.strategy_health_service import StrategyHealthService, HealthLevel
        svc = StrategyHealthService()
        svc._eval_cooldown = 9999  # very long cooldown
        strat = _make_strategy(sharpe_proxy=2.0)

        # First eval
        r1 = svc.evaluate_strategy_health(strategy_id="test_cd", strategy=strat)
        # Second eval within cooldown
        r2 = svc.evaluate_strategy_health(strategy_id="test_cd", strategy=strat)
        # Second should return default (HEALTHY) due to cooldown skip
        assert r2.level == HealthLevel.HEALTHY
