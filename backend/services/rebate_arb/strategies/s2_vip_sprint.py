"""
S2: VIP等级冲刺策略

核心逻辑:
- OKX VIP4: Maker 0%, 需30日$5M交易量
- 阶段性冲刺: 在月末/季末集中刷量达到VIP门槛
- 达标后持续享受0% Maker费率
- 保守ROI 30%, 激进ROI 80%, 回撤15-25%
"""

import logging
from typing import Any, Dict

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S2VIPSprintStrategy:
    """S2: VIP等级冲刺"""

    # OKX VIP等级门槛
    VIP_TIERS = {
        "VIP1": {"volume_30d": 500_000, "maker": 0.00015, "taker": 0.00040},
        "VIP2": {"volume_30d": 2_000_000, "maker": 0.00010, "taker": 0.00035},
        "VIP3": {"volume_30d": 5_000_000, "maker": 0.00005, "taker": 0.00030},
        "VIP4": {"volume_30d": 10_000_000, "maker": 0.0, "taker": 0.00025},
    }

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        if config:
            target = config.get("target_tier")
            volume = config.get("volume_target_30d")
            if target and volume:
                self.VIP_TIERS[target] = self.VIP_TIERS.get(target, {"volume_30d": volume, "maker": 0.0, "taker": 0.00025})

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
        """评估S2策略可行性"""
        okx_data = incentive_data.get("okx", {})
        current_volume = okx_data.get("volume_30d", 0.0)
        current_tier = okx_data.get("tier_name", "VIP0")

        # 找到下一个可冲刺的VIP等级
        target_tier = None
        target_config = None
        for tier_name, config in self.VIP_TIERS.items():
            if current_volume < config["volume_30d"]:
                target_tier = tier_name
                target_config = config
                break

        if not target_tier or not target_config:
            return StrategyEvaluation(
                strategy_type=RebateStrategyType.S2_VIP_SPRINT,
                is_viable=False,
                details={"reason": "已达最高VIP或无冲刺目标"},
            )

        remaining = target_config["volume_30d"] - current_volume

        # 月节省费率估算
        current_maker = 0.0002  # VIP0 maker
        saving_rate = current_maker - target_config["maker"]
        # 预计达标后月交易量
        future_monthly_volume = account_equity * 0.5 * 30
        monthly_saving = future_monthly_volume * saving_rate

        # 冲刺成本 = remaining × 净费率
        sprint_cost = remaining * 0.0003  # 平均成本

        is_viable = (
            account_equity >= 10_000
            and remaining < account_equity * 50  # 冲刺量不超过权益50倍
            and monthly_saving > sprint_cost * 0.1  # 回本期合理
        )

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S2_VIP_SPRINT,
            is_viable=is_viable,
            expected_monthly_value=round(monthly_saving, 2),
            required_volume_usd=remaining,
            risk_score=0.4,
            confidence=0.5,
            details={
                "current_tier": current_tier,
                "target_tier": target_tier,
                "remaining_volume": remaining,
                "sprint_cost": sprint_cost,
                "saving_rate": saving_rate,
                "source_exchange": "okx",
            },
        )

    def build_execution_plan(
        self, size_usd: float, target_tier: str = "VIP3", paper_mode: bool = True
    ) -> Dict[str, Any]:
        """构建VIP冲刺执行计划 — 含标准 side_a + hold/close"""
        config = self.VIP_TIERS.get(target_tier, {})
        base = {
            "strategy": "S2",
            "target_tier": target_tier,
            "required_volume": config.get("volume_30d", 0),
            "daily_target": size_usd,
            "exchange": "okx",
            "symbol": "ETH/USDT:USDT",
            "order_mix": {"limit": 0.7, "market": 0.3},
            "paper_mode": paper_mode,
        }
        try:
            from backend.services.rebate_arb.volume_program_executor import normalize_volume_plan

            return normalize_volume_plan(base, size_usd)
        except Exception:
            return base
