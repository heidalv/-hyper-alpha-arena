"""
RiskGate Agent（胶水层，方案 §1.3 L4 / 红线 R5）。

职责：Target[] → ApprovedTarget[]（Lean 契约 L4 内部，风控削减）。
    - fail-closed：超时/数据缺口/偏差超阈 → 默认拒或削减
    - 硬规则：单标的敞口上限、总敞口上限、方向翻转检测、品种冻结（数据质量）
    - 与执行熔断器（P3.4）联动：熔断状态 → 削减/拒绝

这是 RiskAgent 的 Lean 契约版：Target 进，可能被削减的 ApprovedTarget 出。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.services.contracts.types import ApprovedTarget, OrderAlgo, OrderUrgency, Target

logger = logging.getLogger(__name__)


@dataclass
class RiskGateConfig:
    """风控门配置。"""
    max_single_position_pct: float = 0.20    # 单标的占总仓位上限
    max_total_exposure_pct: float = 1.0      # 总敞口上限
    max_direction_flips_per_day: int = 3     # 方向翻转次数上限
    # 品种冻结（数据质量 GAP → 拒新开仓）
    frozen_symbols: set[str] = None
    # 熔断仓位倍数（来自 ExecutionCircuitBreaker）
    circuit_scale: float = 1.0               # 1.0 正常 / 0.5 降仓 / 0.0 冻结

    def __post_init__(self):
        if self.frozen_symbols is None:
            self.frozen_symbols = set()


class RiskGateAgent:
    """
    Target → ApprovedTarget（fail-closed）。

    用法：
        gate = RiskGateAgent(RiskGateConfig(circuit_scale=0.5))
        approved = gate.review(targets, current_holdings={"BTC": 0.3})
    """

    def __init__(self, config: RiskGateConfig | None = None):
        self.config = config or RiskGateConfig()
        self._flip_counts: dict[str, int] = {}
        self._last_directions: dict[str, str] = {}

    def review(
        self, targets: list[Target],
        current_holdings: Optional[dict[str, float]] = None,
    ) -> list[ApprovedTarget]:
        """
        审核 Target 列表，输出 ApprovedTarget（可能被削减/拒绝）。

        current_holdings: {symbol: current_qty}，用于总敞口/方向翻转判断。
        """
        current = current_holdings or {}
        approved: list[ApprovedTarget] = []

        # 熔断 0 → 全拒
        if self.config.circuit_scale <= 0.0:
            logger.warning("[RiskGate] 熔断冻结（circuit_scale=0），拒绝全部新开仓")
            return []

        # 算总目标敞口
        total_abs = sum(abs(t.target_qty) for t in targets)
        total_abs += sum(abs(v) for v in current.values())

        for t in targets:
            gate_log = []
            sym = t.instrument.symbol
            approved_qty = t.target_qty

            # gate 1: 品种冻结（数据质量）
            if sym in self.config.frozen_symbols:
                logger.info(f"[RiskGate] {sym} 品种冻结，拒绝")
                continue
            gate_log.append("frozen_check:OK")

            # gate 2: 单标的上限
            if abs(approved_qty) > self.config.max_single_position_pct:
                # 削减到上限（符号保留）
                sign = 1.0 if approved_qty >= 0 else -1.0
                approved_qty = sign * self.config.max_single_position_pct
                gate_log.append(f"single_cap:REDUCED→{approved_qty:.4f}")

            # gate 3: 方向翻转检测
            new_dir = "long" if approved_qty > 0 else ("short" if approved_qty < 0 else "flat")
            last_dir = self._last_directions.get(sym, new_dir)
            if last_dir != new_dir and new_dir != "flat":
                flips = self._flip_counts.get(sym, 0) + 1
                self._flip_counts[sym] = flips
                if flips > self.config.max_direction_flips_per_day:
                    logger.warning(f"[RiskGate] {sym} 方向翻转 {flips} 次超限，拒绝")
                    continue
                gate_log.append(f"flip_check:{flips}")
            self._last_directions[sym] = new_dir

            # gate 4: 熔断削减
            if self.config.circuit_scale < 1.0:
                approved_qty *= self.config.circuit_scale
                gate_log.append(f"circuit:SCALED×{self.config.circuit_scale}")

            if abs(approved_qty) < 1e-9:
                continue

            approved.append(ApprovedTarget(
                ts_ns=t.ts_ns, instrument=t.instrument,
                approved_qty=approved_qty,
                # 透传 Target 的算法偏好（原硬编码 MARKET，阶段 3.2 接线）
                algo=getattr(t, "algo", OrderAlgo.MARKET) or OrderAlgo.MARKET,
                urgency=OrderUrgency.NORMAL,
                gate_log=tuple(gate_log),
            ))
        return approved

    def freeze_symbol(self, symbol: str) -> None:
        """冻结品种（数据 GAP/STALE）。"""
        self.config.frozen_symbols.add(symbol)

    def unfreeze_symbol(self, symbol: str) -> None:
        self.config.frozen_symbols.discard(symbol)

    def set_circuit_scale(self, scale: float) -> None:
        """由 ExecutionCircuitBreaker 调用，同步熔断仓位倍数。"""
        self.config.circuit_scale = max(0.0, min(1.0, scale))

    def reset_daily(self) -> None:
        """每日重置方向翻转计数。"""
        self._flip_counts.clear()
