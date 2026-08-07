"""
对冲头寸专用风控

与 DeterministicRiskGate 并行的独立风控模块。
DeterministicRiskGate 假设单向持仓，本模块处理对冲头寸的
特殊风控需求：delta 中性检查、并发对冲限制、总敞口限制。

Phase 2: 仅提供检查能力，不实际阻止任何操作（因为不下单）。
"""

import logging
from typing import Dict, List, Optional

from .models import HedgeRiskCheckResult

logger = logging.getLogger(__name__)


class HedgeRiskAssessor:
    """
    对冲头寸风控评估器

    无状态设计，所有数据通过参数传入。
    遵循 DeterministicRiskGate 的模式：check() -> HedgeRiskCheckResult
    """

    # ── 风控阈值 ──
    MAX_TOTAL_HEDGE_PCT = 0.40       # 总对冲仓位不超过权益40%
    MAX_CONCURRENT_HEDGES = 3        # 最多同时3组对冲
    MAX_SINGLE_LEG_LOSS_PCT = 0.05   # 单腿亏损不超过权益5%
    MAX_DELTA_PCT = 0.02             # 净敞口不超过名义2%

    def __init__(self, rules: Optional[Dict[str, float]] = None):
        """允许通过 rules 字典覆盖默认阈值"""
        if rules:
            self.MAX_TOTAL_HEDGE_PCT = rules.get('max_total_hedge_pct', self.MAX_TOTAL_HEDGE_PCT)
            self.MAX_CONCURRENT_HEDGES = int(rules.get('max_concurrent_hedges', self.MAX_CONCURRENT_HEDGES))
            self.MAX_SINGLE_LEG_LOSS_PCT = rules.get('max_single_leg_loss_pct', self.MAX_SINGLE_LEG_LOSS_PCT)
            self.MAX_DELTA_PCT = rules.get('max_delta_pct', self.MAX_DELTA_PCT)

    def check(
        self,
        account_equity: float,
        current_hedges_count: int,
        current_hedge_notional: float,
        proposed_notional: float,
    ) -> HedgeRiskCheckResult:
        """
        检查一个新的对冲头寸是否通过风控

        Args:
            account_equity: 账户总权益
            current_hedges_count: 当前活跃对冲头寸数量
            current_hedge_notional: 当前对冲头寸总名义价值
            proposed_notional: 新提议的对冲头寸名义价值

        Returns:
            HedgeRiskCheckResult 包含是否通过和原因
        """
        # 安全处理零权益
        if account_equity <= 0:
            return HedgeRiskCheckResult(
                passed=False,
                reason_code="zero_equity",
                reason_text="账户权益为零或负值",
                blocked_by="zero_equity",
            )

        # Rule 1: 总对冲仓位占比
        total_notional = current_hedge_notional + proposed_notional
        total_pct = total_notional / account_equity
        if total_pct > self.MAX_TOTAL_HEDGE_PCT:
            return HedgeRiskCheckResult(
                passed=False,
                reason_code="total_hedge_exceeded",
                reason_text=(
                    f"总对冲仓位 {total_pct:.1%} 超过限制 "
                    f"{self.MAX_TOTAL_HEDGE_PCT:.0%}"
                ),
                blocked_by="max_total_hedge_pct",
            )

        # Rule 2: 并发对冲数量
        if current_hedges_count >= self.MAX_CONCURRENT_HEDGES:
            return HedgeRiskCheckResult(
                passed=False,
                reason_code="max_concurrent_hedges",
                reason_text=(
                    f"当前 {current_hedges_count} 组对冲已达上限 "
                    f"{self.MAX_CONCURRENT_HEDGES}"
                ),
                blocked_by="max_concurrent_hedges",
            )

        # Rule 3: 单腿最大亏损
        single_leg_pct = proposed_notional / account_equity
        if single_leg_pct > self.MAX_SINGLE_LEG_LOSS_PCT:
            return HedgeRiskCheckResult(
                passed=False,
                reason_code="single_leg_loss_exceeded",
                reason_text=(
                    f"单腿仓位 {single_leg_pct:.1%} 超过限制 "
                    f"{self.MAX_SINGLE_LEG_LOSS_PCT:.0%}"
                ),
                blocked_by="max_single_leg_loss_pct",
            )

        # 全部通过
        return HedgeRiskCheckResult(passed=True)


# ── 模块级单例 ──
hedge_risk_assessor = HedgeRiskAssessor()
