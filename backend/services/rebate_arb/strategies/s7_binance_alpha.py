"""
S7: 币安Alpha积分空投策略

核心逻辑:
- 在 Binance Alpha 平台交易赚取积分
- 积分兑换新币空投 (如 TRUTH: 180积分→4000代币)
- 约47.5%的项目毕业上主站
- 首日溢价200-500%
- 保守ROI 50%, 激进ROI 150%, 回撤8-12%
"""

import logging
from typing import Any, Dict

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S7BinanceAlphaStrategy:
    """S7: 币安Alpha积分空投"""

    BINANCE_MAKER = 0.0002
    BINANCE_TAKER = 0.0004

    # Alpha积分参数 (基于TRUTH案例)
    TOKENS_PER_POINT = 22       # 每积分约22代币
    AVG_TOKEN_VALUE = 0.50      # 平均代币价格
    GRADUATION_RATE = 0.475     # 47.5%毕业率
    FIRST_DAY_PREMIUM = 3.0     # 首日平均溢价3倍
    MODE = "monitor_only"

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        try:
            from backend.services.rebate_arb.rule_registry import rule_registry
            registry_params = rule_registry.get_strategy_rule_params("S7")
            self.update_params(registry_params)
        except Exception as e:
            logger.debug("[S7] rule registry params fallback: %s", e)

        if config:
            self.BINANCE_MAKER = config.get("binance_maker", self.BINANCE_MAKER)
            self.BINANCE_TAKER = config.get("binance_taker", self.BINANCE_TAKER)
            self.GRADUATION_RATE = config.get("graduation_rate", self.GRADUATION_RATE)
            self.FIRST_DAY_PREMIUM = config.get("first_day_premium", self.FIRST_DAY_PREMIUM)
            self.MODE = config.get("mode", self.MODE)

    def update_params(self, params: Dict[str, Any]) -> None:
        """运行时更新策略参数"""
        for key, value in params.items():
            upper_key = key.upper()
            if hasattr(self, upper_key):
                setattr(self, upper_key, value)
            elif hasattr(self, key):
                setattr(self, key, value)

    def _recompute_derived(self) -> None:
        """重算派生参数（子类可覆盖）"""
        pass

    def evaluate(self, incentive_data: Dict, account_equity: float) -> StrategyEvaluation:
        """评估S7策略可行性"""
        binance_data = incentive_data.get("binance", {})
        alpha_points = binance_data.get("alpha_points_balance", 0.0)
        daily_points_rate = binance_data.get("alpha_daily_rate", 10.0)

        # 月积分预估
        monthly_points = daily_points_rate * 30

        # 空投价值估算
        estimated_tokens = monthly_points * self.TOKENS_PER_POINT
        token_value = estimated_tokens * self.AVG_TOKEN_VALUE
        expected_after_graduation = token_value * self.GRADUATION_RATE
        # 考虑首日溢价
        expected_with_premium = expected_after_graduation * self.FIRST_DAY_PREMIUM

        # 交易成本 (积分获取需要交易)
        daily_volume = account_equity * 0.2
        monthly_volume = daily_volume * 30
        trading_cost = monthly_volume * self.BINANCE_MAKER

        net_value = expected_with_premium - trading_cost

        is_viable = (
            account_equity >= 3000
            and daily_points_rate > 0
            and net_value > 50
        )
        monitor_only = str(self.MODE or "").lower() == "monitor_only"
        if monitor_only:
            is_viable = False

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S7_BINANCE_ALPHA,
            is_viable=is_viable,
            expected_monthly_value=round(net_value, 2),
            required_volume_usd=monthly_volume,
            risk_score=0.35,
            confidence=0.5,
            details={
                "alpha_points": alpha_points,
                "daily_points_rate": daily_points_rate,
                "monthly_points": monthly_points,
                "estimated_tokens": estimated_tokens,
                "expected_value": expected_with_premium,
                "trading_cost": trading_cost,
                "source_exchange": "binance",
                "mode": self.MODE,
                "monitor_only": monitor_only,
                "rule_sync_required": monitor_only,
            },
        )

    def build_execution_plan(
        self, size_usd: float, paper_mode: bool = True
    ) -> Dict[str, Any]:
        """构建Alpha积分执行计划"""
        return {
            "strategy": "S7",
            "exchange": "binance",
            "target": "alpha_points",
            "daily_volume_target": size_usd,
            "order_mix": {"limit": 0.6, "market": 0.4},
            "paper_mode": paper_mode,
        }
