"""
PortfolioConstruction Agent（胶水层，方案 §1.3 L4 / §7.4）。

职责：Insight[] → Target[]（Lean 契约 L3→L4）。
    - 按 conviction × magnitude × capacity 算目标仓位
    - Kelly 折扣 + 风险预算 + 容量上限约束
    - 归一化到总仓位预算

这是把"方向信号"转成"目标仓位数量"的一层，Alpha 层只管方向/置信，仓位归本层。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.services.contracts.types import Direction, Insight, Instrument, Target

logger = logging.getLogger(__name__)


@dataclass
class PortfolioConfig:
    """组合构建配置。"""
    total_budget_usd: float = 100_000.0      # 总仓位预算
    max_position_pct: float = 0.25            # 单标的最大占比
    kelly_discount: float = 0.5               # Kelly 折扣（防过激）
    min_confidence_to_trade: float = 0.3      # 低于此置信不开仓
    # 价格预言机：callable(symbol) -> price，用于 qty = notional / price
    price_oracle: Optional[callable] = None


class PortfolioConstructionAgent:
    """
    Insight → Target。

    用法：
        agent = PortfolioConstructionAgent(PortfolioConfig(price_oracle=lambda s: 50000))
        targets = agent.construct([insight1, insight2])
    """

    def __init__(self, config: PortfolioConfig | None = None):
        self.config = config or PortfolioConfig()

    def construct(self, insights: list[Insight]) -> list[Target]:
        """把 Insight 列表转成 Target 列表。"""
        # 过滤低置信/FLAT
        actionable = [
            i for i in insights
            if i.confidence >= self.config.min_confidence_to_trade
            and i.direction != Direction.FLAT
        ]
        if not actionable:
            return []

        # 每个 insight 的风险加权 notional
        raw_weights = []
        for ins in actionable:
            # conviction × magnitude 决定风险权重
            w = ins.confidence * max(0.0, min(1.0, abs(ins.magnitude) * 50))
            raw_weights.append(w)
        total_w = sum(raw_weights)
        if total_w < 1e-9:
            return []

        targets: list[Target] = []
        for ins, w in zip(actionable, raw_weights):
            # 归一化 + Kelly 折扣 + 单标的上限
            share = (w / total_w) * self.config.kelly_discount
            share = min(share, self.config.max_position_pct)
            notional = share * self.config.total_budget_usd
            # 方向 → 符号
            sign = 1.0 if ins.direction == Direction.LONG else -1.0
            # notional → qty（需价格）
            qty = self._notional_to_qty(ins.instrument, notional * sign)
            if abs(qty) < 1e-9:
                continue
            targets.append(Target(
                ts_ns=ins.ts_ns, instrument=ins.instrument,
                target_qty=qty,
                reason=f"insight conf={ins.confidence:.2f} mag={ins.magnitude:.4f} src={ins.source}",
            ))
        return targets

    def _notional_to_qty(self, instrument: Instrument, notional: float) -> float:
        """notional USD → qty（需价格）。"""
        if self.config.price_oracle is None:
            # 无价格预言机：用 notional 直接当 qty（测试/占位）
            return notional / max(self.config.total_budget_usd, 1.0)
        try:
            price = self.config.price_oracle(instrument.symbol)
            if not price or price <= 0:
                return 0.0
            return notional / price
        except Exception:
            return 0.0
