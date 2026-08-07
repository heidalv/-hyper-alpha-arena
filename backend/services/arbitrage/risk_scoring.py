"""
复合风险评分算法

将7个风险因子加权计算为0-1的复合评分：
  capital_factor (0.20) + delta_factor (0.15) + funding_factor (0.15)
+ spread_factor (0.15) + fee_factor (0.10) + margin_factor (0.15)
+ liquidity_factor (0.10)

评分等级：
- SAFE (0.0-0.3): 全额开仓
- CAUTION (0.3-0.5): 减仓50%
- DANGER (0.5-0.7): 减仓75%
- CRITICAL (0.7-1.0): 阻止开仓
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .unified_models import (
    ArbAccountSnapshot,
    ArbHedgePosition,
    ArbitrageCapitalPool,
    ArbitrageOpportunity,
    CompositeRiskScore,
    RiskLevel,
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class RiskScorer:
    """复合风险评分计算器"""

    # 权重
    W_CAPITAL: float = 0.20
    W_DELTA: float = 0.15
    W_FUNDING: float = 0.15
    W_SPREAD: float = 0.15
    W_FEE: float = 0.10
    W_MARGIN: float = 0.15
    W_LIQUIDITY: float = 0.10

    def compute(
        self,
        pool: ArbitrageCapitalPool,
        account: ArbAccountSnapshot,
        existing_positions: List[ArbHedgePosition],
        opportunity: Optional[ArbitrageOpportunity] = None,
        proposed_notional: float = 0.0,
        total_cost: float = 0.0,
        expected_profit: float = 0.0,
        available_depth: float = 0.0,
        funding_reversal_count: int = 0,
        max_reversals: int = 3,
        entry_z_threshold: float = 2.0,
        current_z_score: float = 0.0,
        projected_margin_usage: float = 0.0,
        max_margin_usage: float = 0.70,
    ) -> CompositeRiskScore:
        """
        计算复合风险评分

        每个因子归一化到 [0, 1]，加权求和。
        """
        # 1. 资金因子: 池子已用比例
        capital_factor = _clamp(pool.utilization_pct)

        # 2. Delta因子: 已有仓位的总delta
        if existing_positions:
            total_notional = sum(p.notional for p in existing_positions)
            total_delta = sum(abs(p.delta) for p in existing_positions)
            delta_factor = _clamp(total_delta / max(total_notional, 1.0) / 0.02)
        else:
            delta_factor = 0.0

        # 3. 资金费率因子: 反转次数和波动率
        funding_factor = _clamp(funding_reversal_count / max(max_reversals, 1))

        # 4. 价差因子: Z-Score 距离入场阈值的接近度
        if opportunity and entry_z_threshold > 0:
            z_ratio = abs(current_z_score) / entry_z_threshold
            spread_factor = _clamp(1.0 - z_ratio)  # Z越接近阈值，风险越高
        else:
            spread_factor = 0.0

        # 5. 费用因子: 成本占预期利润比例
        if expected_profit > 0:
            fee_factor = _clamp(total_cost / expected_profit)
        else:
            fee_factor = 1.0  # 没有预期利润，最高风险

        # 6. 保证金因子: 预估保证金使用率
        margin_factor = _clamp(projected_margin_usage / max(max_margin_usage, 0.01))

        # 7. 流动性因子: 可用深度是否足够
        if proposed_notional > 0 and available_depth > 0:
            liquidity_factor = _clamp(1.0 - available_depth / (2.0 * proposed_notional))
        else:
            liquidity_factor = 0.5  # 无数据时取中等

        # 加权求和
        total_score = _clamp(
            self.W_CAPITAL * capital_factor
            + self.W_DELTA * delta_factor
            + self.W_FUNDING * funding_factor
            + self.W_SPREAD * spread_factor
            + self.W_FEE * fee_factor
            + self.W_MARGIN * margin_factor
            + self.W_LIQUIDITY * liquidity_factor
        )

        return CompositeRiskScore(
            total_score=total_score,
            capital_factor=capital_factor,
            delta_factor=delta_factor,
            funding_factor=funding_factor,
            spread_factor=spread_factor,
            fee_factor=fee_factor,
            margin_factor=margin_factor,
            liquidity_factor=liquidity_factor,
        )


# ── 模块级单例 ──
risk_scorer = RiskScorer()
