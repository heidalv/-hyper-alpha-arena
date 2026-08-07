"""
[DEPRECATED — M4 已下线，请勿重新启用]
S5: 资金费率+积分叠加策略

下线原因（2026-06 套利中心审计）:
- funding_rates 数据结构假设错误（曾把交易所名当币种）
- 2% 积分叠加收益无规则依据
- 与 V3 资金费套利功能重复
代码保留仅供历史仓位/数据解读；已从 build_all_strategies 注册表移除。

原核心逻辑:
- 在持有资金费率套利仓位的同时获取积分奖励
- 双重收益: 资金费率收入 + 积分价值
"""

import logging
from typing import Any, Dict, Optional

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S5FundingPointsStrategy:
    """S5: 资金费率+积分叠加"""

    HL_MAKER = 0.0002
    POINTS_BONUS_RATE = 0.02  # 积分叠加收益2%

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        if config:
            self.HL_MAKER = config.get("hl_maker", self.HL_MAKER)
            self.POINTS_BONUS_RATE = config.get("points_bonus_rate", self.POINTS_BONUS_RATE)

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

    def evaluate(
        self, incentive_data: Dict, funding_rates: Dict[str, float], account_equity: float
    ) -> StrategyEvaluation:
        """评估S5策略可行性"""
        # funding_rates 实际结构为 {exchange: {symbol: rate}}（exchange_manager 输出），
        # 兼容扁平 {symbol: rate}。旧版把交易所名当币种、dict 当 rate 用，已修复。
        flat_rates: Dict[str, float] = {}
        for key, val in (funding_rates or {}).items():
            if isinstance(val, dict):
                for sym, rate in val.items():
                    try:
                        r = float(rate)
                    except (TypeError, ValueError):
                        continue
                    if sym not in flat_rates or abs(r) > abs(flat_rates[sym]):
                        flat_rates[sym] = r
            else:
                try:
                    flat_rates[key] = float(val)
                except (TypeError, ValueError):
                    continue

        # 找最高绝对值资金费率
        best_rate = 0.0
        best_symbol = ""
        for symbol, rate in flat_rates.items():
            if abs(rate) > abs(best_rate):
                best_rate = rate
                best_symbol = symbol

        if not best_symbol:
            return StrategyEvaluation(
                strategy_type=RebateStrategyType.S5_FUNDING_POINTS,
                is_viable=False,
                details={"reason": "无可用资金费率数据"},
            )

        # 资金费率年化收益
        annual_funding_yield = best_rate * 3 * 365  # 3次/日

        # 积分叠加收益
        hl_data = incentive_data.get("hyperliquid", {})
        points_bonus = hl_data.get("points_bonus_rate", self.POINTS_BONUS_RATE)

        # 仓位大小 = 权益的25%
        position_size = account_equity * 0.25

        # 月资金费率收益
        monthly_funding = position_size * (annual_funding_yield / 12)
        # 月积分收益
        monthly_points = position_size * points_bonus
        # 交易成本 (Maker单)
        trading_cost = position_size * self.HL_MAKER * 2  # 开平仓

        net_monthly = monthly_funding + monthly_points - trading_cost

        is_viable = (
            account_equity >= 200
            and annual_funding_yield > 0.05
            and net_monthly > 5
        )

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S5_FUNDING_POINTS,
            is_viable=is_viable,
            expected_monthly_value=round(net_monthly, 2),
            required_volume_usd=position_size,
            risk_score=0.2,
            confidence=0.75,
            details={
                "best_symbol": best_symbol,
                "funding_rate": best_rate,
                "annual_yield": annual_funding_yield,
                "position_size": position_size,
                "monthly_funding": monthly_funding,
                "monthly_points": monthly_points,
                "source_exchange": "hyperliquid",
            },
        )

    def build_execution_plan(
        self,
        size_usd: float,
        symbol: str = "ETH-PERP",
        side: str = "long",
        paper_mode: bool = True,
        opportunity: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """构建资金费率+积分执行计划 — 标准 side_a 格式"""
        opp = opportunity or {}
        details = opp.get("details") if isinstance(opp.get("details"), dict) else opp
        sym = details.get("best_symbol", symbol) if details else symbol
        funding_side = details.get("funding_side") if details else None
        if not funding_side and details:
            rate = float(details.get("funding_rate", 0) or 0)
            funding_side = "short" if rate > 0 else "long"
        side = side or funding_side or "long"
        order_side = "buy" if side == "long" else "sell"
        return {
            "strategy": "S5",
            "symbol": sym,
            "side_a": {
                "exchange": "hyperliquid",
                "symbol": sym if "/" in sym else f"{sym.replace('-PERP', '')}/USDT:USDT",
                "side": order_side,
                "type": "limit",
                "size_usd": size_usd,
            },
            "side_b": None,
            "hold_duration": "funding_period",
            "paper_mode": paper_mode,
        }
