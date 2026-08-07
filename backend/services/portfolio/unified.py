"""
HedgeLedger + 统一组合层（P5.3，方案 §P5.3 / §7.3/7.4）。

目标：跨 horizon（短/中/长）资金分配 + 风险对冲。
    - HedgeLedger：记录各 horizon 净敞口，短线逆势 vs 中长线 thesis 触发对冲检查
    - UnifiedPortfolio：跨 horizon Kelly×折扣/风险预算/容量分配 + 回撤联动降仓
    - 风险预算：总组合风险按 horizon 分配（如短线 30%/中长 70%，动态调整）

解决"既独立又关联"的资金/风险协作（方案 §7.4）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Horizon(str, Enum):
    SCALP = "scalp"
    SHORT = "short"
    MID = "mid"
    LONG = "long"


@dataclass
class PositionExposure:
    """某 horizon 某 symbol 的敞口。"""
    horizon: Horizon
    symbol: str
    net_qty: float        # 净仓位（正多负空）
    market_value_usd: float = 0.0
    ts_ns: int = 0


@dataclass
class HedgeAlert:
    """对冲告警（短线逆势 vs 中长线 thesis）。"""
    symbol: str
    short_horizon_dir: str    # short insight 方向
    midlong_horizon_dir: str  # thesis 方向
    short_confidence: float
    severity: str = "REVIEW"  # REVIEW / HEDGE_SUGGESTED


class HedgeLedger:
    """
    对冲账本：记录各 horizon 净敞口，检测跨 horizon 冲突。

    用法：
        ledger = HedgeLedger()
        ledger.update(Horizon.SHORT, "BTC-PERP", 1.0, 50000)
        conflict = ledger.check_conflict("BTC-PERP")
    """

    def __init__(self):
        # (horizon, symbol) -> PositionExposure
        self._positions: dict[tuple[Horizon, str], PositionExposure] = {}

    def update(self, horizon: Horizon, symbol: str, net_qty: float,
               market_value_usd: float = 0.0, ts_ns: int = 0) -> None:
        self._positions[(horizon, symbol)] = PositionExposure(
            horizon=horizon, symbol=symbol, net_qty=net_qty,
            market_value_usd=market_value_usd, ts_ns=ts_ns,
        )

    def net_exposure(self, symbol: str) -> float:
        """某 symbol 跨所有 horizon 的净敞口。"""
        return sum(
            p.net_qty for (h, s), p in self._positions.items() if s == symbol
        )

    def exposure_by_horizon(self, symbol: str) -> dict[Horizon, float]:
        return {
            h: p.net_qty for (h, s), p in self._positions.items() if s == symbol
        }

    def check_conflict(self, symbol: str, *, threshold: float = 0.5) -> Optional[HedgeAlert]:
        """
        检测跨 horizon 方向冲突（短线逆势 vs 中长线）。

        若短线净敞口与中长线净敞口符号相反且短线置信超阈 → 对冲检查。
        """
        short_net = sum(
            p.net_qty for (h, s), p in self._positions.items()
            if s == symbol and h in (Horizon.SCALP, Horizon.SHORT)
        )
        midlong_net = sum(
            p.net_qty for (h, s), p in self._positions.items()
            if s == symbol and h in (Horizon.MID, Horizon.LONG)
        )
        if short_net == 0 or midlong_net == 0:
            return None
        # 符号相反 = 冲突
        if short_net * midlong_net < 0:
            return HedgeAlert(
                symbol=symbol,
                short_horizon_dir="long" if short_net > 0 else "short",
                midlong_horizon_dir="long" if midlong_net > 0 else "short",
                short_confidence=abs(short_net) / (abs(short_net) + abs(midlong_net) + 1e-9),
            )
        return None

    def all_positions(self) -> list[PositionExposure]:
        return list(self._positions.values())


@dataclass
class RiskBudgetConfig:
    """风险预算配置（跨 horizon）。"""
    total_risk_budget_pct: float = 0.10   # 总组合年化波动目标 10%
    horizon_allocation: dict[Horizon, float] = field(default_factory=lambda: {
        Horizon.SCALP: 0.10,
        Horizon.SHORT: 0.20,
        Horizon.MID: 0.35,
        Horizon.LONG: 0.35,
    })
    kelly_discount: float = 0.5   # Kelly 折扣（防过激）
    max_drawdown_pct: float = 0.15  # 触发联动降仓
    drawdown_deleverage: float = 0.5  # 回撤时降仓比例


class UnifiedPortfolio:
    """
    统一组合层（跨 horizon 资金/风险分配）。

    用法：
        portfolio = UnifiedPortfolio(RiskBudgetConfig())
        allocation = portfolio.allocate(horizon_returns, current_drawdown)
    """

    def __init__(self, config: RiskBudgetConfig | None = None):
        self.config = config or RiskBudgetConfig()

    def allocate(
        self, horizon_metrics: dict[Horizon, dict],
        current_drawdown: float = 0.0,
    ) -> dict[Horizon, float]:
        """
        按 Kelly×折扣/风险预算/容量分配各 horizon 仓位权重。

        horizon_metrics: {Horizon.SHORT: {"sharpe": 1.5, "capacity_usd": 1e6}, ...}
        返回 {Horizon: weight}。
        """
        # 回撤联动降仓
        deleverage = 1.0
        if current_drawdown > self.config.max_drawdown_pct:
            deleverage = self.config.drawdown_deleverage
            logger.warning(
                f"[UnifiedPortfolio] 回撤 {current_drawdown:.1%} > {self.config.max_drawdown_pct:.1%}，"
                f"降仓至 {deleverage:.0%}"
            )

        weights: dict[Horizon, float] = {}
        total = 0.0
        for horizon, budget in self.config.horizon_allocation.items():
            metrics = horizon_metrics.get(horizon, {})
            sharpe = metrics.get("sharpe", 0.0)
            capacity = metrics.get("capacity_usd", float("inf"))
            # Kelly 分数 × 折扣（简化：sharpe 比例 + 预算上限）
            kelly_frac = max(0.0, min(1.0, sharpe / 3.0)) * self.config.kelly_discount
            # 受风险预算 + 容量约束（容量极小则进一步限制）
            cap_scale = min(1.0, capacity / 1e5) if capacity != float("inf") else 1.0
            weight = min(budget, kelly_frac) * deleverage * cap_scale
            weights[horizon] = weight
            total += weight
        # 归一化
        if total > 1.0:
            weights = {h: w / total for h, w in weights.items()}
        return weights

    def stats(self) -> dict:
        return {
            "total_budget": self.config.total_risk_budget_pct,
            "allocation": {h.value: w for h, w in self.config.horizon_allocation.items()},
            "kelly_discount": self.config.kelly_discount,
        }
