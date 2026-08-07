"""
中长线 Agent 骨架（P5.2，方案 §P5.2 / §7.1）。

目标：thesis 驱动的中长线 Agent（复用现有 MLTO OWM），独立 cadence（小时/日级），
独立风控预算。订阅短线 Insight 作择时 overlay；不改变 thesis 方向，但影响入场时机。

"既独立又关联"（方案 §7.1）：
    独立：各 horizon 有 universe/因子集/cadence/风控预算。
    关联：通过 Alpha Bus 信号共享 + 统一组合层 + 对冲账本。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from backend.services.bus.alpha_bus import AlphaBus, Thesis
from backend.services.contracts.types import Horizon, Insight

logger = logging.getLogger(__name__)


@dataclass
class MidLongConfig:
    """中长线 Agent 配置。"""
    cadence_ns: int = 3600 * 1_000_000_000  # 1 小时
    horizon: Horizon = Horizon.MID
    risk_budget_pct: float = 0.05   # 单 thesis 最大风险占比
    max_theses: int = 10            # 同时活跃 thesis 上限
    use_short_overlay: bool = True  # 订阅短线作择时 overlay


class MidLongAgent:
    """
    中长线 Agent（thesis 驱动）。

    生产：复用 MLTO 的 thesis_store/decision_hub/debate_layer 产出 Thesis。
    当前：提供 thesis 生成 + 发布 + 短线 overlay 订阅框架。
    """

    def __init__(self, bus: AlphaBus, config: MidLongConfig | None = None):
        self.bus = bus
        self.config = config or MidLongConfig()
        self._active_theses: dict[str, Thesis] = {}  # symbol -> thesis
        self._last_run_ns: int = 0

        # 订阅短线 insight 作择时 overlay
        if self.config.use_short_overlay:
            self.bus.subscribe(f"insight_{Horizon.SHORT.value}", self._on_short_insight)

    def generate_thesis(
        self, symbol: str, direction: str, conviction: float,
        target_weight: float, rationale: str = "", ts_ns: int = 0,
    ) -> Optional[Thesis]:
        """
        生成并发布 Thesis。

        生产：由 MLTO debate_layer/decision_hub 产出。
        当前：接收外部参数构造（测试/接入用）。
        """
        if len(self._active_theses) >= self.config.max_theses and symbol not in self._active_theses:
            logger.warning(f"[MidLong] 活跃 thesis 已满({self.config.max_theses})，丢弃 {symbol}")
            return None
        import time
        ts = ts_ns or time.time_ns()
        thesis = Thesis(
            ts_ns=ts, instrument_symbol=symbol, horizon=self.config.horizon,
            direction=direction, conviction=max(0.0, min(1.0, conviction)),
            target_weight=min(target_weight, self.config.risk_budget_pct),
            time_window_ns=self.config.cadence_ns * 24,  # 24 个 cadence 周期有效
            rationale=rationale, source="midlong_agent",
        )
        self._active_theses[symbol] = thesis
        self.bus.publish_thesis(thesis)
        logger.info(f"[MidLong] 发布 thesis {symbol}: {direction} conviction={conviction:.2f}")
        return thesis

    def _on_short_insight(self, insight: Insight) -> None:
        """
        短线 Insight overlay 回调。

        短线与中长线 thesis 强相反时触发对冲检查（不改变 thesis 方向，
        但标记需 PortfolioConstruction 审查）。
        """
        symbol = insight.instrument.symbol
        thesis = self._active_theses.get(symbol)
        if thesis is None:
            return
        # 方向相反
        thesis_dir = thesis.direction.lower()
        insight_dir = insight.direction.value
        opposite = (
            (thesis_dir == "long" and insight_dir == "short")
            or (thesis_dir == "short" and insight_dir == "long")
        )
        if opposite and insight.confidence > 0.6:
            logger.info(
                f"[MidLong] {symbol} 短线逆势(insight={insight_dir}@{insight.confidence:.2f}) "
                f"vs thesis={thesis_dir} → 标记对冲检查"
            )

    def expire_stale(self, now_ns: int) -> int:
        """过期清理超时 thesis。返回清理数。"""
        expired = []
        for sym, t in list(self._active_theses.items()):
            if now_ns - t.ts_ns > t.time_window_ns:
                expired.append(sym)
        for sym in expired:
            del self._active_theses[sym]
        return len(expired)

    def active_theses(self) -> list[Thesis]:
        return list(self._active_theses.values())

    def stats(self) -> dict:
        return {
            "horizon": self.config.horizon.value,
            "active_theses": len(self._active_theses),
            "cadence_hours": self.config.cadence_ns / 3600e9,
        }
