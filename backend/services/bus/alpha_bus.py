"""
Alpha Bus — 跨 horizon 信号总线（P5.1，方案 §P5.1 / §7.2）。

目标：短/中/长信号统一发布订阅。短线与中长线独立运行又关联。
    - 短线 AlphaEnsemble 发布 Insight（horizon=short）
    - 中长线 Agent 发布 Thesis（horizon=mid/long）
    - 互订阅作 overlay：短线订阅中长线 thesis 作方向偏置；中长线订阅短线 insight 作择时

topic 隔离：短/中/长各自独立 topic，避免互相阻塞。
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from backend.services.contracts.types import Horizon, Insight, RegimeLabel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Thesis:
    """
    中长线 Agent 的投资论点（与短线 Insight 对偶）。

    Insight 是短线方向信号（短周期、高频率）；
    Thesis 是中长线结构性论点（长周期、低频率、有逻辑链）。
    """
    ts_ns: int
    instrument_symbol: str
    horizon: Horizon          # mid / long
    direction: str            # long / short / neutral
    conviction: float         # 0..1
    target_weight: float      # 目标仓位权重（组合层消费）
    time_window_ns: int       # 论点有效时间窗
    rationale: str = ""       # 逻辑链（审计）
    source: str = ""          # 哪个中长线 agent


@dataclass
class AlphaBus:
    """
    跨 horizon 信号总线。

    用法：
        bus = AlphaBus()
        bus.subscribe("short_insight", handler)   # 订阅短线信号
        bus.subscribe("mid_thesis", handler)      # 订阅中线论点
        bus.publish_insight(insight)              # 短线发布
        bus.publish_thesis(thesis)                # 中长线发布
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._history: dict[str, list] = defaultdict(list)
        self._max_history = 1000

    def subscribe(self, topic: str, handler: Callable) -> None:
        """订阅 topic。"""
        with self._lock:
            self._subscribers[topic].append(handler)

    def _publish(self, topic: str, item) -> None:
        with self._lock:
            self._history[topic].append(item)
            if len(self._history[topic]) > self._max_history:
                self._history[topic] = self._history[topic][-self._max_history:]
            handlers = list(self._subscribers[topic])
        for h in handlers:
            try:
                h(item)
            except Exception as e:
                logger.error(f"[AlphaBus] topic={topic} handler 异常: {e}", exc_info=False)

    # ==================== 短线信号 ====================

    def publish_insight(self, insight: Insight) -> None:
        """短线 AlphaEnsemble 发布 Insight。"""
        topic = f"insight_{insight.horizon.value}"
        self._publish(topic, insight)

    def publish_scalp_insight(self, insight: Insight) -> None:
        """scalp 信号（最高频，独立 topic 避免阻塞短/中/长）。"""
        self._publish("insight_scalp", insight)

    # ==================== 中长线论点 ====================

    def publish_thesis(self, thesis: Thesis) -> None:
        """中长线 Agent 发布 Thesis。"""
        topic = f"thesis_{thesis.horizon.value}"
        self._publish(topic, thesis)

    # ==================== regime 广播 ====================

    def publish_regime(self, regime: RegimeLabel) -> None:
        """RegimeAgent 广播 regime（所有 horizon 共享）。"""
        self._publish("regime", regime)

    # ==================== 查询 ====================

    def latest_insight(self, horizon: Horizon, symbol: str | None = None):
        """获取最近的 Insight。"""
        topic = f"insight_{horizon.value}"
        with self._lock:
            items = list(self._history.get(topic, []))
        if symbol:
            items = [i for i in items if i.instrument.symbol == symbol]
        return items[-1] if items else None

    def latest_thesis(self, horizon: Horizon, symbol: str | None = None):
        """获取最近的 Thesis。"""
        topic = f"thesis_{horizon.value}"
        with self._lock:
            items = list(self._history.get(topic, []))
        if symbol:
            items = [i for i in items if i.instrument_symbol == symbol]
        return items[-1] if items else None

    def latest_regime(self):
        with self._lock:
            items = list(self._history.get("regime", []))
        return items[-1] if items else None

    def stats(self) -> dict:
        with self._lock:
            return {topic: len(items) for topic, items in self._history.items()}


# 模块级单例
_default_alpha_bus = AlphaBus()


def get_default_alpha_bus() -> AlphaBus:
    return _default_alpha_bus
