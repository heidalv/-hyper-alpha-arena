"""
HedgePositionRiskGate — 对冲头寸风控门

对冲仓位开仓前的确定性风控检查：
1. delta 偏移不超过阈值
2. 总对冲仓位不超过账户净值的比例限制
3. 并发对冲组数不超过上限
4. 资金费率反转检测

设计文档: SYSTEM_UPGRADE_DESIGN_V3.md 第3.5节
"""

import logging
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class HedgePosition:
    """对冲头寸"""
    position_id: str
    symbol: str
    long_size: float
    long_entry_price: float
    short_size: float
    short_entry_price: float
    delta: float                     # 净敞口比例
    accumulated_funding: float = 0   # 累计资金费率收益
    entry_time: datetime = None


@dataclass
class AccountSnapshot:
    """账户快照"""
    total_equity: float
    available_balance: float
    frozen_margin: float = 0


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    passed: bool
    reason_code: str = ""     # delta_exceeded / total_exposure / max_concurrent / funding_reversal / ok
    reason_message: str = ""
    risk_score: float = 0.0   # 0~1, 越高风险越大


class HedgePositionRiskGate:
    """
    对冲仓位风控门

    每次开新对冲仓位前必须通过此检查。
    纯规则实现，不依赖外部ML库。
    """

    # 风控阈值
    MAX_DELTA_PCT: float = 0.02          # 单组 delta 不超过 2%
    MAX_TOTAL_EXPOSURE_PCT: float = 0.40 # 总对冲仓位不超过净值 40%
    MAX_CONCURRENT_HEDGES: int = 3       # 最大并发对冲组数
    FUNDING_REVERSAL_THRESHOLD: int = 3  # 连续反转次数告警

    def check(
        self,
        account: AccountSnapshot,
        existing_hedges: List[HedgePosition],
        new_hedge: HedgePosition,
        funding_history: Optional[List[float]] = None,
    ) -> RiskCheckResult:
        """
        执行全面风控检查

        Args:
            account: 账户快照
            existing_hedges: 现有对冲头寸列表
            new_hedge: 待开仓的新对冲头寸
            funding_history: 最近N期资金费率历史（用于反转检测）

        Returns:
            RiskCheckResult
        """
        risk_factors = []

        # 1. Delta 检查
        if abs(new_hedge.delta) > self.MAX_DELTA_PCT:
            return RiskCheckResult(
                passed=False,
                reason_code="delta_exceeded",
                reason_message=f"delta={new_hedge.delta:.2%} 超过阈值 {self.MAX_DELTA_PCT:.2%}",
                risk_score=abs(new_hedge.delta) / self.MAX_DELTA_PCT,
            )

        # 2. 总对冲仓位检查
        total_exposure = sum(
            max(h.long_size * h.long_entry_price, h.short_size * h.short_entry_price)
            for h in existing_hedges
        )
        new_exposure = max(new_hedge.long_size * new_hedge.long_entry_price,
                          new_hedge.short_size * new_hedge.short_entry_price)
        total_with_new = total_exposure + new_exposure

        if account.total_equity > 0:
            exposure_pct = total_with_new / account.total_equity
            if exposure_pct > self.MAX_TOTAL_EXPOSURE_PCT:
                return RiskCheckResult(
                    passed=False,
                    reason_code="total_exposure",
                    reason_message=f"总对冲仓位 {exposure_pct:.1%} 超过限制 {self.MAX_TOTAL_EXPOSURE_PCT:.1%}",
                    risk_score=exposure_pct / self.MAX_TOTAL_EXPOSURE_PCT,
                )

        # 3. 并发数量检查
        if len(existing_hedges) >= self.MAX_CONCURRENT_HEDGES:
            return RiskCheckResult(
                passed=False,
                reason_code="max_concurrent",
                reason_message=f"已有 {len(existing_hedges)} 组对冲，达到上限 {self.MAX_CONCURRENT_HEDGES}",
                risk_score=0.8,
            )

        # 4. 资金费率反转检测
        if funding_history and len(funding_history) >= 4:
            reversals = 0
            for i in range(1, len(funding_history)):
                if funding_history[i] * funding_history[i - 1] < 0:
                    reversals += 1
            if reversals >= self.FUNDING_REVERSAL_THRESHOLD:
                risk_factors.append("funding_reversal")
                logger.warning(
                    f"[HedgeRiskGate] 资金费率反转 {reversals} 次，对冲风险升高"
                )

        # 综合评分
        risk_score = 0.0
        if abs(new_hedge.delta) > self.MAX_DELTA_PCT * 0.5:
            risk_score += 0.2
        if len(existing_hedges) >= self.MAX_CONCURRENT_HEDGES - 1:
            risk_score += 0.2
        if "funding_reversal" in risk_factors:
            risk_score += 0.3

        return RiskCheckResult(
            passed=True,
            reason_code="ok",
            reason_message="通过风控检查" + (f" (风险因素: {', '.join(risk_factors)})" if risk_factors else ""),
            risk_score=risk_score,
        )
