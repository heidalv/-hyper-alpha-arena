"""
[DEPRECATED — M4 已下线，请勿重新启用]
S1: Maker返佣对冲策略

下线原因（2026-06 套利中心审计）:
- 数学 EV 为负：月返佣约 $0.45 vs 月成本约 $40
- 与 S6 跨所费率差重复且更差
- Asterdex Stage 6 官方明确惩罚对冲刷分（wash trade 取消资格）
代码保留仅供历史仓位/数据解读；已从 build_all_strategies 注册表移除。

原核心逻辑（基于已过期的旧赛季假设）:
- Asterdex做Maker(0.005%费率 + 10%返佣 = 净赚0.045%)
- Binance做对冲腿(0.04% Taker成本)
- 组合净收入: -0.005%（几乎零成本）
"""

import logging
import time
from typing import Any, Dict, Optional

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S1MakerHedgeStrategy:
    """S1: Maker返佣对冲"""

    # Asterdex费率
    ASTERDEX_MAKER = 0.00005       # 0.005%
    ASTERDEX_REBATE = 0.10          # 10%返佣
    # Binance对冲腿
    BINANCE_TAKER = 0.0004          # 0.04%

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        if config:
            self.ASTERDEX_MAKER = config.get("asterdex_maker", self.ASTERDEX_MAKER)
            self.ASTERDEX_REBATE = config.get("asterdex_rebate", self.ASTERDEX_REBATE)
            self.BINANCE_TAKER = config.get("binance_taker", self.BINANCE_TAKER)

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

    # 最低启动资金（USDT）
    MIN_EQUITY = 100

    def evaluate(self, incentive_data: Dict, account_equity: float) -> StrategyEvaluation:
        """评估S1策略可行性"""
        # 从 incentive_data 获取实际费率
        asterdex_data = incentive_data.get("asterdex", {})
        actual_rebate = asterdex_data.get("rebate_rate", self.ASTERDEX_REBATE)
        actual_maker = asterdex_data.get("maker_rate", self.ASTERDEX_MAKER)

        # Asterdex Maker净成本 = maker_fee - rebate_income
        # rebate_income = maker_fee * rebate_rate
        # 实际: 0.005% - (0.005% × 10%) = 0.005% - 0.0005% = 0.0045% 成本
        asterdex_net_cost = actual_maker * (1 - actual_rebate)

        # 对冲腿成本
        binance_data = incentive_data.get("binance", {})
        binance_taker = binance_data.get("taker_rate", self.BINANCE_TAKER)

        # 组合单次往返成本 = Asterdex Maker + Binance Taker
        # = 0.0045% + 0.04% = 0.0445% (约4.5bps)
        round_trip_cost = asterdex_net_cost + binance_taker

        # 策略收益来源: Asterdex 的 10%返佣
        # 返佣收入 = 交易额 × maker费率 × 返佣率
        rebate_income_rate = actual_maker * actual_rebate  # 0.005% × 10% = 0.0005%

        # 月交易量: 300U资金可用5-20x杠杆，日均做1-3轮
        # 保守估算: 权益×杠杆×次数 / 天 = equity * 5 * 2 = equity * 10/天
        daily_volume = account_equity * 10  # 5x杠杆 × 2轮/天
        monthly_volume = daily_volume * 30

        # 月净收益 = 返佣收入 - 对冲成本
        # 注意: S1的核心不是"赚价差"而是"Asterdex返佣 > 净成本"
        # 实际：返佣收入 0.0005%很小，主要靠量大
        gross_rebate = monthly_volume * rebate_income_rate
        net_cost = monthly_volume * round_trip_cost
        expected_monthly = gross_rebate  # 返佣是净额外收入

        # 真实情况：S1核心收益 = Asterdex返佣，对冲腿确保无方向性风险
        # 但净成本(0.0445%)远大于返佣(0.0005%)，需要价差或资金费率配合
        # 修正：如果对冲是同方向+反向平仓（做市），净成本更低
        # 实际可行场景：Asterdex做Maker限价单，等成交后Binance市价对冲
        # 真实净收入 = -asterdex_maker + rebate - binance_taker ≈ -0.0445%
        # 只在 rebate > total_cost 时才有正收益，目前需要更多返佣
        actual_net = gross_rebate - net_cost

        rh_per_1k = asterdex_data.get("rh_per_1k_usd", 0.1)
        monthly_rh_value = (monthly_volume / 1000) * rh_per_1k * 0.01
        combined_net = actual_net + monthly_rh_value

        is_viable = (
            actual_rebate > 0
            and account_equity >= self.MIN_EQUITY
            and combined_net > 0
        )

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S1_MAKER_HEDGE,
            is_viable=is_viable,
            expected_monthly_value=round(max(combined_net, 0), 2),
            required_volume_usd=monthly_volume,
            risk_score=0.15,
            confidence=0.85,
            details={
                "round_trip_cost_pct": round_trip_cost,
                "rebate_income_rate": rebate_income_rate,
                "asterdex_rebate": actual_rebate,
                "asterdex_maker": actual_maker,
                "binance_taker": binance_taker,
                "daily_volume": daily_volume,
                "gross_rebate": gross_rebate,
                "net_monthly": actual_net,
                "rh_bonus_monthly": monthly_rh_value,
                "combined_net": combined_net,
                "source_exchange": "asterdex",
                "target_exchange": "binance",
                "min_equity": self.MIN_EQUITY,
                "leverage_assumption": "5x",
            },
        )

    def build_execution_plan(
        self,
        size_usd: float,
        symbol: str = "ETH/USDT",
        paper_mode: bool = True,
    ) -> Dict[str, Any]:
        """构建执行计划"""
        return {
            "strategy": "S1",
            "side_a": {
                "exchange": "asterdex",
                "symbol": symbol,
                "side": "buy",
                "type": "limit",  # Maker单
                "size_usd": size_usd,
            },
            "side_b": {
                "exchange": "binance",
                "symbol": symbol,
                "side": "sell",  # 对冲
                "type": "market",
                "size_usd": size_usd,
            },
            "expected_rebate": size_usd * self.ASTERDEX_MAKER * self.ASTERDEX_REBATE,
            "expected_cost": size_usd * self.BINANCE_TAKER,
            "paper_mode": paper_mode,
        }
