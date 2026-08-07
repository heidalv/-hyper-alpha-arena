"""
跨 horizon 风控熔断（P5.4，方案 §P5.4）。

目标：极端 regime（连环清算/黑天鹅）时，跨 horizon 总敞口自动降。

与 P3.4 执行熔断的区别：
    P3.4 单 horizon 执行层熔断（影子偏差）。
    P5.4 跨 horizon 组合层熔断（总敞口/极端 regime）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from backend.services.alpha.regime_refined import Regime
from backend.services.portfolio.unified import HedgeLedger

logger = logging.getLogger(__name__)


class CrossHorizonState(str, Enum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"       # 总敞口降至 50%
    EMERGENCY = "EMERGENCY"   # 总敞口降至 0（全平）


@dataclass
class CrossHorizonConfig:
    """跨 horizon 熔断配置。"""
    max_total_exposure_pct: float = 1.0   # 总敞口上限（占净值）
    reduce_threshold_pct: float = 0.8     # 超 80% → REDUCED
    emergency_regimes: tuple = (Regime.LIQUIDATION_CASCADE, Regime.EXTREME)
    emergency_portfolio_drawdown: float = 0.20  # 组合回撤 20% → EMERGENCY


class CrossHorizonCircuitBreaker:
    """
    跨 horizon 风控熔断。

    用法：
        cb = CrossHorizonCircuitBreaker(ledger)
        state = cb.assess(total_exposure_usd, net_equity_usd, regime, drawdown)
        if state == CrossHorizonState.EMERGENCY:
            # 全平
    """

    def __init__(self, ledger: HedgeLedger, config: CrossHorizonConfig | None = None):
        self.ledger = ledger
        self.config = config or CrossHorizonConfig()
        self.state = CrossHorizonState.NORMAL
        self._reason = ""

    def assess(
        self, total_exposure_usd: float, net_equity_usd: float,
        regime: str = "", portfolio_drawdown: float = 0.0,
    ) -> CrossHorizonState:
        """评估跨 horizon 风险状态。"""
        exposure_pct = total_exposure_usd / max(net_equity_usd, 1.0)
        # EMERGENCY：极端 regime 或 组合回撤超阈
        if regime in self.config.emergency_regimes:
            self._transition(CrossHorizonState.EMERGENCY,
                             f"极端 regime={regime}")
        elif portfolio_drawdown > self.config.emergency_portfolio_drawdown:
            self._transition(CrossHorizonState.EMERGENCY,
                             f"组合回撤 {portfolio_drawdown:.1%}")
        # REDUCED：总敞口超阈
        elif exposure_pct > self.config.reduce_threshold_pct:
            self._transition(CrossHorizonState.REDUCED,
                             f"总敞口 {exposure_pct:.1%} > {self.config.reduce_threshold_pct:.1%}")
        # NORMAL
        else:
            self._transition(CrossHorizonState.NORMAL, "风险正常")
        return self.state

    def target_exposure_scale(self) -> float:
        """当前目标总敞口倍数。"""
        if self.state == CrossHorizonState.EMERGENCY:
            return 0.0
        if self.state == CrossHorizonState.REDUCED:
            return 0.5
        return 1.0

    def _transition(self, new_state: CrossHorizonState, reason: str) -> None:
        if new_state != self.state:
            logger.warning(
                f"[CrossHorizonBreaker] {self.state} → {new_state}: {reason}"
            )
            self.state = new_state
            self._reason = reason

    def stats(self) -> dict:
        return {"state": self.state.value, "reason": self._reason,
                "exposure_scale": self.target_exposure_scale()}
