"""
S4: 交易竞赛套利策略

核心逻辑:
- 参与交易所举办的交易竞赛/活动
- 利用返利策略降低参赛成本
- 奖池分配按排名
- 保守ROI 15%, 激进ROI 60%, 回撤20-30%
"""

import logging
from typing import Any, Dict, List

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S4CampaignArbStrategy:
    """S4: 交易竞赛套利"""

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        self._min_expected_roi_pct = 2.0
        if config:
            self._min_expected_roi_pct = config.get("min_expected_roi_pct", self._min_expected_roi_pct)

    def update_params(self, params: Dict[str, Any]) -> None:
        """运行时更新策略参数"""
        for key, value in params.items():
            upper_key = key.upper()
            if hasattr(self, upper_key):
                setattr(self, upper_key, value)
            elif hasattr(self, key):
                setattr(self, key, value)
        # Recompute derived values
        self._recompute_derived()

    def _recompute_derived(self) -> None:
        """重算派生参数（子类可覆盖）"""
        pass

    def evaluate(self, incentive_data: Dict, account_equity: float) -> StrategyEvaluation:
        """评估S4策略可行性"""
        campaigns: List[Dict] = incentive_data.get("active_campaigns", [])

        if not campaigns:
            return StrategyEvaluation(
                strategy_type=RebateStrategyType.S4_CAMPAIGN_ARB,
                is_viable=False,
                details={"reason": "无活跃竞赛"},
            )

        # 评估最有价值的竞赛
        best_campaign = max(campaigns, key=lambda c: c.get("prize_pool_usd", 0))
        prize_pool = best_campaign.get("prize_pool_usd", 0)
        min_volume = best_campaign.get("min_volume_usd", 0)
        deadline_days = best_campaign.get("deadline_days", 30)

        # 竞争力评估 (保守10%获奖概率)
        win_probability = 0.10
        expected_reward = prize_pool * win_probability

        # 参赛成本
        trading_cost_rate = 0.0003  # 平均费率
        cost = min_volume * trading_cost_rate

        net_value = expected_reward - cost

        is_viable = (
            account_equity >= 5000
            and net_value > 0
            and deadline_days > 1
            and prize_pool > 1000
        )

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S4_CAMPAIGN_ARB,
            is_viable=is_viable,
            expected_monthly_value=round(net_value, 2),
            required_volume_usd=min_volume,
            risk_score=0.6,
            confidence=0.3,
            details={
                "campaign_name": best_campaign.get("name", "unknown"),
                "prize_pool": prize_pool,
                "min_volume": min_volume,
                "deadline_days": deadline_days,
                "expected_reward": expected_reward,
                "cost": cost,
            },
        )

    def build_execution_plan(
        self, size_usd: float, campaign_id: str = "", paper_mode: bool = True
    ) -> Dict[str, Any]:
        """构建竞赛套利执行计划"""
        base = {
            "strategy": "S4",
            "campaign_id": campaign_id,
            "daily_volume_target": size_usd,
            "exchange": "okx",
            "symbol": "ETH/USDT:USDT",
            "order_mix": {"limit": 0.5, "market": 0.5},
            "paper_mode": paper_mode,
        }
        try:
            from backend.services.rebate_arb.volume_program_executor import normalize_volume_plan

            return normalize_volume_plan(base, size_usd)
        except Exception:
            return base
