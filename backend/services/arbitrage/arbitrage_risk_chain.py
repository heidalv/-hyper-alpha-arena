"""
统一套利风控链

将8项预交易检查串行执行，首个失败即停止。
同时集成复合风险评分用于仓位规模调整。

替代原有的 HedgeRiskAssessor + HedgePositionRiskGate 的部分功能。
与主系统的 DeterministicRiskGate 并行运行。

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3.5节
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .unified_models import (
    ArbAccountSnapshot,
    ArbHedgePosition,
    ArbRiskCheckResult,
    ArbitrageCapitalPool,
    ArbitrageOpportunity,
    CompositeRiskScore,
    RiskLevel,
)
from .fee_schedule import fee_registry
from .pre_trade_checks import (
    CapitalPoolCheck,
    ConcurrentPositionCheck,
    CrossExchangeExposureCheck,
    DeltaExposureCheck,
    FeeImpactCheck,
    FundingStabilityCheck,
    MarginSufficiencyCheck,
    SpreadLiquidityCheck,
)
from .risk_scoring import risk_scorer

logger = logging.getLogger(__name__)


class ArbitrageRiskChain:
    """
    统一套利风控链

    串行执行8项检查，快速失败。
    全部通过后计算复合风险评分。
    """

    def __init__(self):
        self._checks = [
            ("capital_pool", CapitalPoolCheck()),
            ("cross_exchange_exposure", CrossExchangeExposureCheck()),
            ("delta_exposure", DeltaExposureCheck()),
            ("funding_stability", FundingStabilityCheck()),
            ("spread_liquidity", SpreadLiquidityCheck()),
            ("fee_impact", FeeImpactCheck()),
            ("margin_sufficiency", MarginSufficiencyCheck()),
            ("concurrent_position", ConcurrentPositionCheck()),
        ]

    def check_pre_trade(
        self,
        pool: ArbitrageCapitalPool,
        account: ArbAccountSnapshot,
        opportunity: ArbitrageOpportunity,
        existing_positions: List[ArbHedgePosition],
        proposed_notional: float,
        proposed_delta: float = 0.0,
        funding_history: Optional[List[float]] = None,
        orderbook_depth_a: float = 0.0,
        orderbook_depth_b: float = 0.0,
        is_cross_exchange: bool = True,
        margin_requirement: float = 0.0,
    ) -> Tuple[ArbRiskCheckResult, Optional[CompositeRiskScore]]:
        """
        执行完整预交易风控链

        Returns:
            (最终检查结果, 复合风险评分)
            如果任一检查失败，risk_score 为 None
        """
        results: List[ArbRiskCheckResult] = []
        is_cross = is_cross_exchange and bool(opportunity.exchange_a and opportunity.exchange_b)

        # 计算费用
        if is_cross:
            total_cost = fee_registry.cross_exchange_round_trip_cost(
                proposed_notional, opportunity.exchange_a, opportunity.exchange_b
            )
        else:
            exchange = opportunity.exchange_a or "hyperliquid"
            total_cost = fee_registry.single_exchange_round_trip_cost(proposed_notional, exchange)

        # 预期利润（简化估算：基于年化收益按持仓24h计算）
        expected_profit = proposed_notional * opportunity.expected_annual_yield / 365.0

        # 逐项执行
        for name, check in self._checks:
            result = self._run_check(
                name, check, pool, account, opportunity, existing_positions,
                proposed_notional, proposed_delta, funding_history,
                orderbook_depth_a, orderbook_depth_b, is_cross,
                total_cost, expected_profit, margin_requirement,
            )
            results.append(result)

            if not result.passed:
                logger.warning(
                    f"[ArbRiskChain] 风控链在 {name} 处被阻止: "
                    f"{result.reason_text or result.reason_code}"
                )
                return result, None

        # 全部通过 → 计算复合风险评分
        reversal_count = 0
        if funding_history and len(funding_history) >= 4:
            window = funding_history[-12:]
            for i in range(1, len(window)):
                if window[i] * window[i - 1] < 0:
                    reversal_count += 1

        score = risk_scorer.compute(
            pool=pool,
            account=account,
            existing_positions=existing_positions,
            opportunity=opportunity,
            proposed_notional=proposed_notional,
            total_cost=total_cost,
            expected_profit=expected_profit,
            available_depth=orderbook_depth_a,
            funding_reversal_count=reversal_count,
            projected_margin_usage=account.frozen_margin + margin_requirement,
        )

        passed_result = ArbRiskCheckResult(
            passed=True,
            check_name="risk_chain",
            reason_code="all_passed",
            reason_text=f"全部 {len(results)} 项检查通过，风险评分 {score.total_score:.2f} ({score.level.value})",
            risk_score=score.total_score,
        )

        logger.info(
            f"[ArbRiskChain] 风控链通过: score={score.total_score:.2f}, "
            f"level={score.level.value}, size_multiplier={score.size_multiplier:.2f}"
        )
        return passed_result, score

    def _run_check(
        self,
        name: str,
        check: Any,
        pool: ArbitrageCapitalPool,
        account: ArbAccountSnapshot,
        opportunity: ArbitrageOpportunity,
        existing_positions: List[ArbHedgePosition],
        proposed_notional: float,
        proposed_delta: float,
        funding_history: Optional[List[float]],
        depth_a: float,
        depth_b: float,
        is_cross: bool,
        total_cost: float,
        expected_profit: float,
        margin_requirement: float,
    ) -> ArbRiskCheckResult:
        """路由到对应检查项"""
        try:
            if name == "capital_pool":
                return check.check(pool, proposed_notional, margin_requirement)
            elif name == "cross_exchange_exposure":
                return check.check(account, existing_positions, opportunity, proposed_notional)
            elif name == "delta_exposure":
                return check.check(existing_positions, proposed_delta, proposed_notional)
            elif name == "funding_stability":
                return check.check(funding_history)
            elif name == "spread_liquidity":
                return check.check(proposed_notional, depth_a, depth_b, is_cross)
            elif name == "fee_impact":
                return check.check(expected_profit, total_cost)
            elif name == "margin_sufficiency":
                return check.check(account, margin_requirement, margin_requirement * 0.5)
            elif name == "concurrent_position":
                return check.check(existing_positions, opportunity.strategy, opportunity.symbol)
            else:
                return ArbRiskCheckResult(passed=True, check_name=name, reason_code="unknown_skip")
        except Exception as e:
            logger.error(f"[ArbRiskChain] 检查 {name} 异常: {e}")
            return ArbRiskCheckResult(
                passed=False,
                check_name=name,
                reason_code="check_error",
                reason_text=f"检查异常: {e}",
                blocked_by="internal_error",
            )


# ── 模块级单例 ──
arbitrage_risk_chain = ArbitrageRiskChain()
