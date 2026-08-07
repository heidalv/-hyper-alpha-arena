"""
执行熔断器（P3.4，方案 §P3.4，红线 R5）。

目标：fail-closed。监控驱动，非人工开关。
- 影子偏差连续 critical → 自动降仓 50%
- 更严重 → 冻结新开仓（平仓仍允许）
- 数据质量 GAP/STALE → 拒绝该品种新开仓
- 连环清算/极端 regime → 跨 horizon 总敞口降

消费 DualTrackExecutor 的 ShadowDeviation + QualityGate 的 DataQualityFlag。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from backend.services.execution.client import ShadowDeviation

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    NORMAL = "NORMAL"
    THROTTLED = "THROTTLED"      # 降仓 50%
    FROZEN = "FROZEN"             # 冻结新开仓
    EMERGENCY = "EMERGENCY"       # 全平


@dataclass
class CircuitBreakerConfig:
    """熔断阈值。"""
    consecutive_critical_to_throttle: int = 3   # 连续 3 次 critical → 降仓
    consecutive_critical_to_freeze: int = 5      # 连续 5 次 → 冻结
    warn_ratio_to_throttle: float = 0.5          # warn 偏差占比 > 50% → 降仓
    cooldown_ms: float = 60000.0                  # 冷却 1 分钟


class ExecutionCircuitBreaker:
    """
    执行熔断器。

    用法：
        cb = ExecutionCircuitBreaker()
        cb.observe_deviation(deviation)
        if not cb.can_open_position(symbol):
            # 冻结，拒绝新开仓
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.NORMAL
        self._consecutive_critical = 0
        self._frozen_symbols: set[str] = set()
        self._history: list[ShadowDeviation] = []
        self._state_changes: list[tuple[float, CircuitState, str]] = []

    def observe_deviation(self, dev: ShadowDeviation) -> CircuitState:
        """观察一次影子偏差，更新熔断状态。返回当前状态。"""
        self._history.append(dev)
        if len(self._history) > 1000:
            self._history = self._history[-1000:]

        if dev.severity == "CRITICAL":
            self._consecutive_critical += 1
        elif dev.severity in ("OK", "WARN"):
            self._consecutive_critical = 0  # 重置

        if self._consecutive_critical >= self.config.consecutive_critical_to_freeze:
            self._transition(CircuitState.FROZEN, f"连续 {self._consecutive_critical} 次 critical 偏差")
        elif self._consecutive_critical >= self.config.consecutive_critical_to_throttle:
            self._transition(CircuitState.THROTTLED, f"连续 {self._consecutive_critical} 次 critical 偏差")
        else:
            # 检查 warn 比例
            recent = self._history[-20:]
            if recent:
                warn_ratio = sum(1 for d in recent if d.severity in ("WARN", "CRITICAL")) / len(recent)
                if warn_ratio > self.config.warn_ratio_to_throttle:
                    self._transition(CircuitState.THROTTLED, f"偏差占比 {warn_ratio:.0%} 超阈")
                elif self.state != CircuitState.NORMAL:
                    self._transition(CircuitState.NORMAL, "偏差回落")
        return self.state

    def freeze_symbol(self, symbol: str, reason: str = "quality") -> None:
        """冻结特定品种新开仓（数据质量/异常）。"""
        self._frozen_symbols.add(symbol)
        logger.warning(f"[CircuitBreaker] 冻结 {symbol} 新开仓: {reason}")

    def unfreeze_symbol(self, symbol: str) -> None:
        self._frozen_symbols.discard(symbol)

    def can_open_position(self, symbol: str) -> bool:
        """是否允许该品种新开仓（fail-closed：冻结时 False）。"""
        if self.state == CircuitState.FROZEN or self.state == CircuitState.EMERGENCY:
            return False
        if symbol in self._frozen_symbols:
            return False
        return True

    def position_scale(self) -> float:
        """当前允许的仓位倍数（降仓时 0.5，正常 1.0，冻结 0.0）。"""
        if self.state in (CircuitState.FROZEN, CircuitState.EMERGENCY):
            return 0.0
        if self.state == CircuitState.THROTTLED:
            return 0.5
        return 1.0

    def _transition(self, new_state: CircuitState, reason: str) -> None:
        if new_state != self.state:
            import time
            self._state_changes.append((time.time(), new_state, reason))
            logger.warning(
                f"[CircuitBreaker] {self.state} → {new_state}: {reason}"
            )
            self.state = new_state

    def stats(self) -> dict:
        return {
            "state": self.state.value,
            "consecutive_critical": self._consecutive_critical,
            "frozen_symbols": list(self._frozen_symbols),
            "history_size": len(self._history),
            "state_changes": len(self._state_changes),
        }

    def reset(self) -> None:
        self.state = CircuitState.NORMAL
        self._consecutive_critical = 0
        self._frozen_symbols.clear()
        self._history.clear()
