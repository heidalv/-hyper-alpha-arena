"""WebSocket 广播服务 — v3 整改

面向"AI 学习系统整合"的三类事件真实广播：
  - drl_advice_update       （对应 subscribe_drl_advice）
  - kelly_allocation_update （对应 subscribe_kelly_allocation）
  - evolution_progress_update（对应 subscribe_evolution_progress）

设计原则：
  1. 订阅集按 topic 维护，连接断开自动清理。
  2. 广播统一经 ConnectionManager.schedule_task 调度，兼容同步调用方。
  3. 所有调用点带节流，避免决策高频时刷屏。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)


TOPIC_DRL = "drl_advice"
TOPIC_KELLY = "kelly_allocation"
TOPIC_EVOLUTION = "evolution_progress"
TOPIC_COORDINATOR = "coordinator_status"  # P2-1 AI 闭环协调器状态
TOPIC_KLINES = "klines"  # K 线实时推送
TOPIC_LEARNING = "learning_events"  # 统一进化学习内核血缘事件（EvolutionEnvelope）

_VALID_TOPICS = {TOPIC_DRL, TOPIC_KELLY, TOPIC_EVOLUTION, TOPIC_COORDINATOR, TOPIC_KLINES, TOPIC_LEARNING}


class _WSBroadcastHub:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[Any]] = {t: set() for t in _VALID_TOPICS}
        self._lock = threading.Lock()
        # 节流：topic -> last_ts
        self._last_ts: Dict[Any, float] = {}
        # 每 topic 最小间隔（秒），防止主循环高频广播造成带宽压力
        self._min_interval: Dict[str, float] = {
            TOPIC_DRL: 1.0,
            TOPIC_KELLY: 2.0,
            TOPIC_EVOLUTION: 0.0,  # 进化低频，不限流
            TOPIC_COORDINATOR: 5.0,  # 协调器状态至少 5s 间隔（一般 30s 推一次）
            TOPIC_KLINES: 1.0,  # K 线推送间隔 1s
            TOPIC_LEARNING: 0.0,  # 血缘事件低频且需完整，不限流
        }

    # ─── 订阅管理 ───

    def subscribe(self, ws: Any, topic: str) -> bool:
        if topic not in _VALID_TOPICS:
            return False
        with self._lock:
            self._subscribers[topic].add(ws)
        return True

    def unsubscribe(self, ws: Any, topic: str) -> None:
        if topic not in _VALID_TOPICS:
            return
        with self._lock:
            self._subscribers[topic].discard(ws)

    def unsubscribe_all(self, ws: Any) -> None:
        """连接断开时全部清理"""
        with self._lock:
            for sub_set in self._subscribers.values():
                sub_set.discard(ws)

    def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._subscribers.get(topic, set()))

    # ─── 广播 ───

    async def _broadcast_async(self, topic: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers.get(topic, set()))
        if not targets:
            return
        message = json.dumps({"type": event_type, "topic": topic, "data": payload})
        dead = []
        for ws in targets:
            try:
                if getattr(ws, "client_state", None) is not None and ws.client_state.name != "CONNECTED":
                    dead.append(ws)
                    continue
                await ws.send_text(message)
            except Exception as e:
                logger.debug(f"[WSBroadcast] topic={topic} send failed: {e}")
                dead.append(ws)
        if dead:
            with self._lock:
                for d in dead:
                    self._subscribers.get(topic, set()).discard(d)

    def broadcast(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        throttle: bool = True,
        throttle_key: Any = None,
    ) -> None:
        """同步入口：由 manager.schedule_task 调度 coroutine，兼容主交易线程。"""
        if topic not in _VALID_TOPICS:
            return
        if throttle:
            now = time.time()
            min_gap = self._min_interval.get(topic, 0.0)
            key = (topic, throttle_key)
            last = self._last_ts.get(key, 0.0)
            if min_gap > 0.0 and (now - last) < min_gap:
                return
            self._last_ts[key] = now
        try:
            from backend.api.ws import manager  # 延迟导入避免启动期循环依赖
            manager.schedule_task(self._broadcast_async(topic, event_type, payload))
        except Exception as e:
            logger.debug(f"[WSBroadcast] schedule_task 失败 topic={topic}: {e}")

    # ─── 语义化封装 ───

    def broadcast_drl_update(self, payload: Dict[str, Any]) -> None:
        self.broadcast(TOPIC_DRL, "drl_advice_update", payload)

    def broadcast_kelly_update(self, payload: Dict[str, Any]) -> None:
        self.broadcast(TOPIC_KELLY, "kelly_allocation_update", payload)

    def broadcast_evolution_update(self, payload: Dict[str, Any]) -> None:
        self.broadcast(TOPIC_EVOLUTION, "evolution_progress_update", payload, throttle=False)

    def broadcast_coordinator_status(self, payload: Dict[str, Any]) -> None:
        """P2-1 — 推送协调器 / 学习闭环状态给前端 Banner。"""
        self.broadcast(TOPIC_COORDINATOR, "coordinator_status_update", payload)

    def broadcast_learning_event(self, payload: Dict[str, Any]) -> None:
        """统一进化学习内核 — 推送一条 EvolutionEnvelope 血缘事件到"进化中枢"实时管线。"""
        self.broadcast(TOPIC_LEARNING, "learning_event", payload, throttle=False)


ws_broadcast_hub = _WSBroadcastHub()
