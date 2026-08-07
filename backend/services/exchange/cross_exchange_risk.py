"""
CrossExchangeRiskTracker — 跨交易所风控管理

包含跨交易所敞口追踪、相关性计算、单腿风险管理。
设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3.5节
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.services.exchange.base_exchange_client import (
    BaseExchangeClient,
    ExchangeOrder,
    ExchangePosition,
    OrderSide,
    OrderType,
)

logger = logging.getLogger(__name__)


# 风控规则默认参数
DEFAULT_RISK_RULES = {
    'max_hedge_delta_pct': 0.02,        # 对冲头寸最大净敞口 2%
    'max_total_arbitrage_pct': 0.40,    # 总套利仓位占权益比 40%
    'max_cross_exchange_exposure': 0.20, # 单交易所最大敞口 20%
}


@dataclass
class CrossExchangeExposure:
    """跨交易所敞口追踪"""
    exchange: str
    total_margin: float
    total_notional: float
    position_count: int
    symbols: List[str] = field(default_factory=list)


@dataclass
class CrossExchangeRiskCheckResult:
    """跨交易所风控检查结果"""
    passed: bool
    violations: List[str] = field(default_factory=list)
    exposure_a: Optional[CrossExchangeExposure] = None
    exposure_b: Optional[CrossExchangeExposure] = None
    correlation: float = 0.0

    @property
    def is_safe(self) -> bool:
        return self.passed


class CrossExchangeRiskTracker:
    """
    跨交易所风控追踪器

    功能：
    1. 计算两交易所头寸的相关性
    2. 检查敞口限制
    3. 计算单交易所风险暴露
    """

    def __init__(self, rules: Optional[Dict] = None):
        self._rules = rules or dict(DEFAULT_RISK_RULES)

    def calculate_correlation(
        self,
        positions_a: List[ExchangePosition],
        positions_b: List[ExchangePosition],
    ) -> float:
        """计算两交易所头寸的重叠度"""
        symbols_a = {p.symbol for p in positions_a}
        symbols_b = {p.symbol for p in positions_b}
        overlap = symbols_a & symbols_b
        if not overlap or not symbols_a or not symbols_b:
            return 0.0
        return len(overlap) / max(len(symbols_a), len(symbols_b))

    def calculate_exposure(
        self,
        positions: List[ExchangePosition],
        exchange_name: str,
    ) -> CrossExchangeExposure:
        """计算单交易所敞口"""
        total_margin = sum(p.margin for p in positions)
        total_notional = sum(p.notional_value for p in positions)
        symbols = list({p.symbol for p in positions})
        return CrossExchangeExposure(
            exchange=exchange_name,
            total_margin=total_margin,
            total_notional=total_notional,
            position_count=len(positions),
            symbols=symbols,
        )

    def check_risk(
        self,
        positions_a: List[ExchangePosition],
        positions_b: List[ExchangePosition],
        equity: float,
    ) -> CrossExchangeRiskCheckResult:
        """
        全面风控检查

        规则：
        1. max_hedge_delta_pct: 对冲头寸最大净敞口
        2. max_total_arbitrage_pct: 总套利仓位占权益比
        3. max_cross_exchange_exposure: 单交易所最大敞口
        """
        violations = []

        exposure_a = self.calculate_exposure(positions_a, "exchange_a")
        exposure_b = self.calculate_exposure(positions_b, "exchange_b")
        correlation = self.calculate_correlation(positions_a, positions_b)

        if equity <= 0:
            return CrossExchangeRiskCheckResult(
                passed=False,
                violations=["equity <= 0"],
                exposure_a=exposure_a,
                exposure_b=exposure_b,
                correlation=correlation,
            )

        # Rule: 总套利仓位占权益比
        total_notional = exposure_a.total_notional + exposure_b.total_notional
        total_arb_pct = total_notional / equity
        if total_arb_pct > self._rules['max_total_arbitrage_pct']:
            violations.append(
                f"total_arbitrage_pct={total_arb_pct:.2%} > "
                f"{self._rules['max_total_arbitrage_pct']:.2%}"
            )

        # Rule: 单交易所最大敞口
        for exp in (exposure_a, exposure_b):
            exp_pct = exp.total_notional / equity
            if exp_pct > self._rules['max_cross_exchange_exposure']:
                violations.append(
                    f"{exp.exchange} exposure={exp_pct:.2%} > "
                    f"{self._rules['max_cross_exchange_exposure']:.2%}"
                )

        # Rule: 对冲头寸净敞口
        symbols_a = {p.symbol: p for p in positions_a}
        symbols_b = {p.symbol: p for p in positions_b}
        overlap = set(symbols_a.keys()) & set(symbols_b.keys())
        for sym in overlap:
            pa = symbols_a[sym]
            pb = symbols_b[sym]
            delta = abs(pa.notional_value - pb.notional_value)
            delta_pct = delta / equity
            if delta_pct > self._rules['max_hedge_delta_pct']:
                violations.append(
                    f"{sym} hedge delta={delta_pct:.2%} > "
                    f"{self._rules['max_hedge_delta_pct']:.2%}"
                )

        return CrossExchangeRiskCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            exposure_a=exposure_a,
            exposure_b=exposure_b,
            correlation=correlation,
        )

    @property
    def rules(self) -> Dict:
        """获取当前风控规则"""
        return dict(self._rules)


class LegRiskManager:
    """
    单腿风险管理器

    当跨交易所套利中一侧订单成功、另一侧失败时的应急处理。
    """

    MAX_SINGLE_LEG_DURATION = 60     # 单腿暴露最长60秒
    MAX_SINGLE_LEG_LOSS = 0.02       # 单腿最大亏损2%
    MAX_RETRIES = 3                  # 最大重试次数

    async def handle_single_leg(
        self,
        executed_leg: ExchangeOrder,
        failed_leg: ExchangeOrder,
        client: BaseExchangeClient,
    ) -> bool:
        """
        处理单腿暴露

        策略1：重试失败腿（最多3次）
        策略2：重试失败则平掉成功腿
        """
        # 策略1: 重试失败腿
        for retry in range(self.MAX_RETRIES):
            try:
                result = await client.place_order(failed_leg)
                if isinstance(result, dict) and result.get('status') != 'error':
                    return True
            except Exception as e:
                logger.warning(
                    "LegRiskManager retry %d/%d failed: %s",
                    retry + 1, self.MAX_RETRIES, e,
                )
                await asyncio.sleep(1)

        # 策略2: 平掉成功腿
        close_side = (
            OrderSide.SELL if executed_leg.side == OrderSide.BUY
            else OrderSide.BUY
        )
        close_order = ExchangeOrder(
            order_id=f"emergency_close_{executed_leg.order_id}",
            symbol=executed_leg.symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            size=executed_leg.size,
            reduce_only=True,
        )
        try:
            await client.place_order(close_order)
            logger.warning(
                "Emergency close executed for %s (%s)",
                executed_leg.symbol, executed_leg.order_id,
            )
            return False
        except Exception as e:
            logger.error(
                "Emergency close FAILED for %s: %s",
                executed_leg.symbol, e,
            )
            return False
