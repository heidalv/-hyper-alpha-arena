"""
8项预交易风控检查（串行，快速失败）

每项检查返回 ArbRiskCheckResult，首个失败即停止链路。
所有检查为纯规则计算，不依赖 LLM。

检查顺序：
1. 资金池可用性
2. 跨交易所敞口限制
3. Delta 暴露限制
4. 资金费率稳定性
5. 价差流动性检查
6. 费用影响分析
7. 保证金充足性
8. 并发仓位限制
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from .unified_models import (
    ArbAccountSnapshot,
    ArbHedgePosition,
    ArbRiskCheckResult,
    ArbitrageCapitalPool,
    ArbitrageOpportunity,
)
from .fee_schedule import fee_registry

logger = logging.getLogger(__name__)


class CapitalPoolCheck:
    """检查1: 资金池可用性"""

    def check(
        self,
        pool: ArbitrageCapitalPool,
        proposed_notional: float,
        margin_requirement: float = 0.0,
    ) -> ArbRiskCheckResult:
        # 冷却期检查
        if time.time() < pool.cooldown_until:
            remaining = int(pool.cooldown_until - time.time())
            return ArbRiskCheckResult(
                passed=False,
                check_name="capital_pool",
                reason_code="cooldown",
                reason_text=f"资金池处于冷却期，剩余 {remaining}s",
                blocked_by="cooldown",
            )

        # 每日亏损限制
        daily_loss_limit = pool.total_pool_usd * pool.daily_loss_limit_pct
        if pool.daily_realized_loss >= daily_loss_limit:
            return ArbRiskCheckResult(
                passed=False,
                check_name="capital_pool",
                reason_code="daily_loss_exceeded",
                reason_text=f"日亏损 {pool.daily_realized_loss:.2f} 已达限制 {daily_loss_limit:.2f}",
                blocked_by="daily_loss_limit",
            )

        # 可用资金检查
        required = proposed_notional + margin_requirement
        if not pool.can_allocate(required):
            return ArbRiskCheckResult(
                passed=False,
                check_name="capital_pool",
                reason_code="insufficient_pool",
                reason_text=f"资金池可用 {pool.available_usd:.2f}，需要 {required:.2f}",
                blocked_by="insufficient_pool",
            )

        return ArbRiskCheckResult(passed=True, check_name="capital_pool", reason_code="ok")


class CrossExchangeExposureCheck:
    """检查2: 跨交易所敞口限制"""

    MAX_SINGLE_EXCHANGE_PCT: float = 0.20
    MAX_TOTAL_ARBITRAGE_PCT: float = 0.40

    def check(
        self,
        account: ArbAccountSnapshot,
        existing_positions: List[ArbHedgePosition],
        opportunity: ArbitrageOpportunity,
        proposed_notional: float,
    ) -> ArbRiskCheckResult:
        if account.total_equity <= 0:
            return ArbRiskCheckResult(
                passed=False,
                check_name="cross_exchange_exposure",
                reason_code="zero_equity",
                blocked_by="zero_equity",
            )

        # 按交易所聚合同前的名义价值
        per_exchange: Dict[str, float] = {}
        for p in existing_positions:
            for ex in (p.exchange_long, p.exchange_short):
                if ex:
                    per_exchange[ex] = per_exchange.get(ex, 0.0) + p.notional / 2

        # 添加新仓位的敞口
        new_exchanges = [e for e in (opportunity.exchange_a, opportunity.exchange_b) if e]
        for ex in new_exchanges:
            per_exchange[ex] = per_exchange.get(ex, 0.0) + proposed_notional / 2

        # 单交易所限制
        for ex, notional in per_exchange.items():
            pct = notional / account.total_equity
            if pct > self.MAX_SINGLE_EXCHANGE_PCT:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="cross_exchange_exposure",
                    reason_code="single_exchange_exceeded",
                    reason_text=f"{ex} 敞口 {pct:.1%} 超过限制 {self.MAX_SINGLE_EXCHANGE_PCT:.0%}",
                    blocked_by="single_exchange_limit",
                    details={"exchange": ex, "pct": pct},
                )

        # 总套利敞口限制
        total = sum(p.notional for p in existing_positions) + proposed_notional
        total_pct = total / account.total_equity
        if total_pct > self.MAX_TOTAL_ARBITRAGE_PCT:
            return ArbRiskCheckResult(
                passed=False,
                check_name="cross_exchange_exposure",
                reason_code="total_exceeded",
                reason_text=f"总套利敞口 {total_pct:.1%} 超过限制 {self.MAX_TOTAL_ARBITRAGE_PCT:.0%}",
                blocked_by="total_exposure_limit",
            )

        return ArbRiskCheckResult(passed=True, check_name="cross_exchange_exposure", reason_code="ok")


class DeltaExposureCheck:
    """检查3: Delta 暴露限制"""

    MAX_SINGLE_HEDGE_DELTA_PCT: float = 0.02
    MAX_PORTFOLIO_DELTA_PCT: float = 0.05

    def check(
        self,
        existing_positions: List[ArbHedgePosition],
        new_delta: float,
        new_notional: float,
    ) -> ArbRiskCheckResult:
        # 单组 Delta 检查
        if new_notional > 0:
            new_delta_pct = abs(new_delta) / new_notional
            if new_delta_pct > self.MAX_SINGLE_HEDGE_DELTA_PCT:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="delta_exposure",
                    reason_code="single_delta_exceeded",
                    reason_text=f"单组 delta {new_delta_pct:.2%} 超过限制 {self.MAX_SINGLE_HEDGE_DELTA_PCT:.2%}",
                    blocked_by="single_delta_limit",
                )

        # 组合 Delta 检查
        total_notional = sum(p.notional for p in existing_positions) + new_notional
        total_delta = sum(abs(p.delta) for p in existing_positions) + abs(new_delta)
        if total_notional > 0:
            portfolio_delta_pct = total_delta / total_notional
            if portfolio_delta_pct > self.MAX_PORTFOLIO_DELTA_PCT:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="delta_exposure",
                    reason_code="portfolio_delta_exceeded",
                    reason_text=f"组合 delta {portfolio_delta_pct:.2%} 超过限制 {self.MAX_PORTFOLIO_DELTA_PCT:.2%}",
                    blocked_by="portfolio_delta_limit",
                )

        return ArbRiskCheckResult(passed=True, check_name="delta_exposure", reason_code="ok")


class FundingStabilityCheck:
    """检查4: 资金费率稳定性"""

    REVERSAL_WINDOW: int = 12
    MAX_REVERSALS: int = 3

    def check(
        self,
        funding_history: Optional[List[float]] = None,
    ) -> ArbRiskCheckResult:
        if not funding_history or len(funding_history) < 4:
            return ArbRiskCheckResult(
                passed=True,
                check_name="funding_stability",
                reason_code="insufficient_history",
                reason_text="历史数据不足，跳过稳定性检查",
            )

        # 计算反转次数
        window = funding_history[-self.REVERSAL_WINDOW:]
        reversals = 0
        for i in range(1, len(window)):
            if window[i] * window[i - 1] < 0:
                reversals += 1

        if reversals >= self.MAX_REVERSALS:
            return ArbRiskCheckResult(
                passed=False,
                check_name="funding_stability",
                reason_code="funding_reversal",
                reason_text=f"最近 {self.REVERSAL_WINDOW} 期资金费率反转 {reversals} 次，超过阈值 {self.MAX_REVERSALS}",
                blocked_by="funding_reversal",
                risk_score=reversals / self.MAX_REVERSALS,
            )

        return ArbRiskCheckResult(
            passed=True,
            check_name="funding_stability",
            reason_code="ok",
            risk_score=reversals / self.MAX_REVERSALS,
        )


class SpreadLiquidityCheck:
    """检查5: 价差流动性检查"""

    MIN_DEPTH_MULTIPLIER: float = 2.0

    def check(
        self,
        proposed_notional: float,
        orderbook_depth_a: float = 0.0,
        orderbook_depth_b: float = 0.0,
        is_cross_exchange: bool = True,
    ) -> ArbRiskCheckResult:
        if proposed_notional <= 0:
            return ArbRiskCheckResult(passed=True, check_name="spread_liquidity", reason_code="ok")

        min_required = proposed_notional * self.MIN_DEPTH_MULTIPLIER

        if is_cross_exchange:
            # 双腿都需要通过
            if orderbook_depth_a < min_required:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="spread_liquidity",
                    reason_code="insufficient_depth_a",
                    reason_text=f"交易所A深度 {orderbook_depth_a:.0f} < 需要 {min_required:.0f}",
                    blocked_by="liquidity_a",
                )
            if orderbook_depth_b < min_required:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="spread_liquidity",
                    reason_code="insufficient_depth_b",
                    reason_text=f"交易所B深度 {orderbook_depth_b:.0f} < 需要 {min_required:.0f}",
                    blocked_by="liquidity_b",
                )
        else:
            available = max(orderbook_depth_a, 0.0)
            if available < min_required:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="spread_liquidity",
                    reason_code="insufficient_depth",
                    reason_text=f"市场深度 {available:.0f} < 需要 {min_required:.0f}",
                    blocked_by="liquidity",
                )

        return ArbRiskCheckResult(passed=True, check_name="spread_liquidity", reason_code="ok")


class FeeImpactCheck:
    """检查6: 费用影响分析"""

    MIN_PROFIT_COST_RATIO: float = 1.5

    def check(
        self,
        expected_profit: float,
        total_cost: float,
    ) -> ArbRiskCheckResult:
        if total_cost <= 0:
            return ArbRiskCheckResult(passed=True, check_name="fee_impact", reason_code="ok")

        net_profit = expected_profit - total_cost
        if net_profit <= 0:
            return ArbRiskCheckResult(
                passed=False,
                check_name="fee_impact",
                reason_code="net_negative",
                reason_text=f"净收益为负: 利润={expected_profit:.4f}, 成本={total_cost:.4f}",
                blocked_by="net_negative",
            )

        profit_cost_ratio = net_profit / total_cost
        if profit_cost_ratio < self.MIN_PROFIT_COST_RATIO:
            return ArbRiskCheckResult(
                passed=False,
                check_name="fee_impact",
                reason_code="low_profit_ratio",
                reason_text=f"利润成本比 {profit_cost_ratio:.2f} < 最低 {self.MIN_PROFIT_COST_RATIO}",
                blocked_by="low_profit_ratio",
                risk_score=1.0 - profit_cost_ratio / self.MIN_PROFIT_COST_RATIO,
            )

        return ArbRiskCheckResult(passed=True, check_name="fee_impact", reason_code="ok")


class MarginSufficiencyCheck:
    """检查7: 保证金充足性"""

    MARGIN_BUFFER_PCT: float = 0.15
    MAX_MARGIN_USAGE_PCT: float = 0.70

    def check(
        self,
        account: ArbAccountSnapshot,
        projected_initial_margin: float,
        projected_maintenance_margin: float,
    ) -> ArbRiskCheckResult:
        # 可用余额 + 缓冲
        required = projected_initial_margin * (1 + self.MARGIN_BUFFER_PCT)
        if account.available_balance < required:
            return ArbRiskCheckResult(
                passed=False,
                check_name="margin_sufficiency",
                reason_code="insufficient_margin",
                reason_text=f"可用余额 {account.available_balance:.2f} < 需要 {required:.2f}",
                blocked_by="margin_shortage",
            )

        # 保证金使用率检查
        total_margin = account.frozen_margin + projected_initial_margin
        margin_usage = total_margin / max(account.total_equity, 1.0)
        if margin_usage > self.MAX_MARGIN_USAGE_PCT:
            return ArbRiskCheckResult(
                passed=False,
                check_name="margin_sufficiency",
                reason_code="margin_usage_exceeded",
                reason_text=f"保证金使用率 {margin_usage:.1%} > 限制 {self.MAX_MARGIN_USAGE_PCT:.0%}",
                blocked_by="margin_usage",
            )

        return ArbRiskCheckResult(passed=True, check_name="margin_sufficiency", reason_code="ok")


class ConcurrentPositionCheck:
    """检查8: 并发仓位限制"""

    MAX_CONCURRENT: int = 3
    MAX_PER_TYPE: int = 2
    MAX_PER_SYMBOL: int = 1

    def check(
        self,
        existing_positions: List[ArbHedgePosition],
        strategy_type: str = "",
        symbol: str = "",
    ) -> ArbRiskCheckResult:
        # 总并发数
        if len(existing_positions) >= self.MAX_CONCURRENT:
            return ArbRiskCheckResult(
                passed=False,
                check_name="concurrent_position",
                reason_code="max_concurrent",
                reason_text=f"已有 {len(existing_positions)} 个仓位，达到上限 {self.MAX_CONCURRENT}",
                blocked_by="max_concurrent",
            )

        # 按类型统计
        if strategy_type:
            type_count = sum(1 for p in existing_positions if p.strategy.value == strategy_type)
            if type_count >= self.MAX_PER_TYPE:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="concurrent_position",
                    reason_code="max_per_type",
                    reason_text=f"策略 {strategy_type} 已有 {type_count} 个仓位，达到上限 {self.MAX_PER_TYPE}",
                    blocked_by="max_per_type",
                )

        # 按交易对统计
        if symbol:
            symbol_count = sum(1 for p in existing_positions if p.symbol == symbol)
            if symbol_count >= self.MAX_PER_SYMBOL:
                return ArbRiskCheckResult(
                    passed=False,
                    check_name="concurrent_position",
                    reason_code="max_per_symbol",
                    reason_text=f"{symbol} 已有 {symbol_count} 个仓位，达到上限 {self.MAX_PER_SYMBOL}",
                    blocked_by="max_per_symbol",
                )

        return ArbRiskCheckResult(passed=True, check_name="concurrent_position", reason_code="ok")
