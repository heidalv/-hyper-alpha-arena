"""
S6: 跨所费率差套利策略

核心逻辑:
- 利用 Asterdex 极低费率(0.005% Maker) vs 主流所(0.04% Taker)
- A腿: Asterdex Maker (净-0.045%)
- B腿: 主流所 Taker (0.04%)
- 组合净收入: 几乎为零或微利
- 保守ROI 20%, 激进ROI 40%, 回撤5-8%
"""

import logging
from typing import Any, Dict

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S6CrossFeeSpreadStrategy:
    """S6: 跨所费率差套利"""

    ASTERDEX_MAKER = 0.00005
    ASTERDEX_REBATE = 0.10
    TARGET_TAKER = 0.0004  # Binance taker

    # 最低启动资金
    MIN_EQUITY = 200

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        if config:
            self.ASTERDEX_MAKER = config.get("asterdex_maker", self.ASTERDEX_MAKER)
            self.ASTERDEX_REBATE = config.get("asterdex_rebate", self.ASTERDEX_REBATE)
            self.TARGET_TAKER = config.get("target_taker", self.TARGET_TAKER)

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
        """评估S6策略可行性"""
        asterdex_data = incentive_data.get("asterdex", {})
        binance_data = incentive_data.get("binance", {})

        actual_maker = asterdex_data.get("maker_rate", self.ASTERDEX_MAKER)
        actual_rebate = asterdex_data.get("rebate_rate", self.ASTERDEX_REBATE)
        target_taker = binance_data.get("taker_rate", self.TARGET_TAKER)

        # Asterdex 净成本 (扣除返佣后)
        asterdex_net = actual_maker * (1 - actual_rebate)

        # S6的真实逻辑：两边同时开仓对冲
        # A腿: Asterdex 做Maker开多 → 费率极低(0.0045%)
        # B腿: Binance 做Taker开空 → 费率(0.04%)
        # 组合总成本 = asterdex_net + target_taker = 0.0445%
        # 收益来源：
        # 1. Asterdex返佣（虽然小但确定）
        # 2. Rh积分（在Asterdex交易的额外奖励）
        # 3. 资金费率差（如果有利方向）
        total_cost_per_trade = asterdex_net + target_taker
        rebate_income = actual_maker * actual_rebate

        # 月交易量：小资金用杠杆
        leverage = min(10, max(5, 30000 / max(account_equity, 1)))
        daily_volume = account_equity * leverage * 2
        monthly_volume = daily_volume * 30

        # 月返佣收益
        monthly_rebate = monthly_volume * rebate_income

        # 月交易成本
        monthly_cost = monthly_volume * total_cost_per_trade

        # 注意：S6纯靠费率差是亏的，核心价值是：
        # 1. 用极低成本做对冲仓位
        # 2. 在Asterdex累积Rh积分（→ ASTER空投）
        # 3. 适合与S8组合使用
        net_monthly = monthly_rebate - monthly_cost

        # 与S8组合考虑：Rh积分价值
        rh_per_1k_usd = asterdex_data.get("rh_per_1k_usd", 0.1)
        monthly_rh_value = (monthly_volume / 1000) * rh_per_1k_usd * 0.01  # 保守估值

        combined_monthly = net_monthly + monthly_rh_value

        # M4 收紧：必须真实正 EV 才算可行。
        # 旧版「combined_monthly > -5 允许微亏」是伪可行门槛；
        # 且 Stage 6 官方惩罚对冲刷分，S6 的对冲模式本身有取消资格风险。
        is_viable = (
            account_equity >= self.MIN_EQUITY
            and actual_rebate > 0
            and combined_monthly > 0
        )

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S6_CROSS_FEE_SPREAD,
            is_viable=is_viable,
            # 不再用 max(...,0) 掩盖亏损，负 EV 如实展示
            expected_monthly_value=round(combined_monthly, 2),
            required_volume_usd=monthly_volume,
            risk_score=0.2,
            confidence=0.8,
            details={
                "total_cost_per_trade": total_cost_per_trade,
                "rebate_income_rate": rebate_income,
                "asterdex_net": asterdex_net,
                "target_taker": target_taker,
                "leverage": leverage,
                "daily_volume": daily_volume,
                "monthly_rebate": monthly_rebate,
                "monthly_cost": monthly_cost,
                "monthly_rh_value": monthly_rh_value,
                "source_exchange": "asterdex",
                "target_exchange": "binance",
                "min_equity": self.MIN_EQUITY,
                "api_automatable": True,
            },
        )

    def build_execution_plan(
        self, size_usd: float, symbol: str = "ETH/USDT", paper_mode: bool = True
    ) -> Dict[str, Any]:
        """构建跨所费率差执行计划"""
        return {
            "strategy": "S6",
            "side_a": {
                "exchange": "asterdex",
                "symbol": symbol,
                "side": "buy",
                "type": "limit",
                "size_usd": size_usd,
            },
            "side_b": {
                "exchange": "binance",
                "symbol": symbol,
                "side": "sell",
                "type": "market",
                "size_usd": size_usd,
            },
            "paper_mode": paper_mode,
        }
