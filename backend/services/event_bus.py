"""
Event Bus — 异步事件总线 (F3-1)

基于 asyncio.Queue 的发布-订阅事件系统，替代 90s tick 轮询。
可与现有 tick 循环共存（渐进迁移）。

事件类型:
- MarketDataEvent: 价格更新 / 成交量异常 / K线完成
- RiskEvent: 爆仓警告 / 保证金不足 / 日亏损熔断
- SignalEvent: 技术指标交叉 / 突破 / 背离
- DecisionEvent: AI 决策完成 / Orchestrator 信号
- OrderEvent: 成交 / 部分成交 / 取消 / 拒绝
"""

import asyncio
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
#  事件类型定义
# ══════════════════════════════════════════════════


class EventPriority(Enum):
    LOW = 0       # 市场数据更新
    NORMAL = 1    # 信号生成
    HIGH = 2      # 决策执行
    CRITICAL = 3  # 风险事件（优先处理）


class EventType(Enum):
    # 市场数据
    PRICE_UPDATE = "price_update"
    VOLUME_ANOMALY = "volume_anomaly"
    KLINE_CLOSED = "kline_closed"
    FUNDING_RATE_UPDATE = "funding_rate_update"

    # 风险
    LIQUIDATION_WARNING = "liquidation_warning"
    MARGIN_INSUFFICIENT = "margin_insufficient"
    DAILY_LOSS_BREAKER = "daily_loss_breaker"
    CIRCUIT_BREAKER_TRIGGERED = "circuit_breaker_triggered"
    RISK_SCORE_CHANGE = "risk_score_change"

    # 信号
    INDICATOR_CROSS = "indicator_cross"
    BREAKOUT = "breakout"
    DIVERGENCE = "divergence"
    REGIME_CHANGE = "regime_change"

    # 决策
    AI_DECISION_READY = "ai_decision_ready"
    ORCHESTRATOR_SIGNAL = "orchestrator_signal"
    STRATEGY_PAUSED = "strategy_paused"
    STRATEGY_RESUMED = "strategy_resumed"

    # 订单
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    POSITION_CLOSED = "position_closed"


@dataclass
class BaseEvent:
    """事件基类"""
    event_type: EventType
    priority: EventPriority = EventPriority.NORMAL
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # 事件来源模块
    symbol: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def iso_time(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


@dataclass
class MarketDataEvent(BaseEvent):
    """市场数据事件"""
    event_type: EventType = EventType.PRICE_UPDATE

    @classmethod
    def price_update(
        cls, symbol: str, price: float, volume: float = 0,
        source: str = "market_data",
    ) -> "MarketDataEvent":
        return cls(
            event_type=EventType.PRICE_UPDATE,
            priority=EventPriority.LOW,
            source=source,
            symbol=symbol,
            data={"price": price, "volume": volume},
        )

    @classmethod
    def kline_closed(
        cls, symbol: str, timeframe: str, ohlcv: Dict[str, Any],
        source: str = "kline_collector",
    ) -> "MarketDataEvent":
        return cls(
            event_type=EventType.KLINE_CLOSED,
            priority=EventPriority.NORMAL,
            source=source,
            symbol=symbol,
            data={"timeframe": timeframe, "ohlcv": ohlcv},
        )

    @classmethod
    def volume_anomaly(
        cls, symbol: str, current_vol: float, avg_vol: float, ratio: float,
        source: str = "market_scanner",
    ) -> "MarketDataEvent":
        return cls(
            event_type=EventType.VOLUME_ANOMALY,
            priority=EventPriority.NORMAL,
            source=source,
            symbol=symbol,
            data={"current_vol": current_vol, "avg_vol": avg_vol, "ratio": ratio},
        )


@dataclass
class RiskEvent(BaseEvent):
    """风险事件"""
    event_type: EventType = EventType.RISK_SCORE_CHANGE

    @classmethod
    def liquidation_warning(
        cls, symbol: str, distance_pct: float, liquidation_price: float,
        source: str = "liquidation_monitor",
    ) -> "RiskEvent":
        return cls(
            event_type=EventType.LIQUIDATION_WARNING,
            priority=EventPriority.CRITICAL,
            source=source,
            symbol=symbol,
            data={"distance_pct": distance_pct, "liquidation_price": liquidation_price},
        )

    @classmethod
    def daily_loss_breaker(
        cls, account_id: int, loss_ratio: float, daily_pnl: float,
        source: str = "risk_control",
    ) -> "RiskEvent":
        return cls(
            event_type=EventType.DAILY_LOSS_BREAKER,
            priority=EventPriority.CRITICAL,
            source=source,
            data={"account_id": account_id, "loss_ratio": loss_ratio, "daily_pnl": daily_pnl},
        )

    @classmethod
    def risk_score_change(
        cls, account_id: int, old_score: float, new_score: float,
        source: str = "risk_control",
    ) -> "RiskEvent":
        return cls(
            event_type=EventType.RISK_SCORE_CHANGE,
            priority=EventPriority.HIGH,
            source=source,
            data={"account_id": account_id, "old_score": old_score, "new_score": new_score},
        )


@dataclass
class SignalEvent(BaseEvent):
    """信号事件"""
    event_type: EventType = EventType.INDICATOR_CROSS

    @classmethod
    def indicator_cross(
        cls, symbol: str, indicator: str, direction: str, value: float,
        source: str = "signal_detection",
    ) -> "SignalEvent":
        return cls(
            event_type=EventType.INDICATOR_CROSS,
            priority=EventPriority.NORMAL,
            source=source,
            symbol=symbol,
            data={"indicator": indicator, "direction": direction, "value": value},
        )

    @classmethod
    def breakout(
        cls, symbol: str, level: float, direction: str, confidence: float,
        source: str = "signal_detection",
    ) -> "SignalEvent":
        return cls(
            event_type=EventType.BREAKOUT,
            priority=EventPriority.HIGH,
            source=source,
            symbol=symbol,
            data={"level": level, "direction": direction, "confidence": confidence},
        )

    @classmethod
    def regime_change(
        cls, symbol: str, old_regime: str, new_regime: str, confidence: float,
        source: str = "market_regime",
    ) -> "SignalEvent":
        return cls(
            event_type=EventType.REGIME_CHANGE,
            priority=EventPriority.HIGH,
            source=source,
            symbol=symbol,
            data={"old_regime": old_regime, "new_regime": new_regime, "confidence": confidence},
        )


@dataclass
class DecisionEvent(BaseEvent):
    """决策事件"""
    event_type: EventType = EventType.AI_DECISION_READY

    @classmethod
    def ai_decision_ready(
        cls, symbol: str, action: str, confidence: float, strategy_id: str = "",
        source: str = "ai_decision",
    ) -> "DecisionEvent":
        return cls(
            event_type=EventType.AI_DECISION_READY,
            priority=EventPriority.HIGH,
            source=source,
            symbol=symbol,
            data={"action": action, "confidence": confidence, "strategy_id": strategy_id},
        )

    @classmethod
    def orchestrator_signal(
        cls, symbol: str, final_action: str, position_scale: float, tier: str = "",
        source: str = "orchestrator",
    ) -> "DecisionEvent":
        return cls(
            event_type=EventType.ORCHESTRATOR_SIGNAL,
            priority=EventPriority.HIGH,
            source=source,
            symbol=symbol,
            data={"final_action": final_action, "position_scale": position_scale, "tier": tier},
        )


@dataclass
class OrderEvent(BaseEvent):
    """订单事件"""
    event_type: EventType = EventType.ORDER_FILLED

    @classmethod
    def filled(
        cls, symbol: str, side: str, size: float, price: float, order_id: str = "",
        source: str = "order_executor",
    ) -> "OrderEvent":
        return cls(
            event_type=EventType.ORDER_FILLED,
            priority=EventPriority.NORMAL,
            source=source,
            symbol=symbol,
            data={"side": side, "size": size, "price": price, "order_id": order_id},
        )

    @classmethod
    def position_closed(
        cls, symbol: str, pnl: float, pnl_pct: float, reason: str = "",
        source: str = "position_tracker",
    ) -> "OrderEvent":
        return cls(
            event_type=EventType.POSITION_CLOSED,
            priority=EventPriority.NORMAL,
            source=source,
            symbol=symbol,
            data={"pnl": pnl, "pnl_pct": pnl_pct, "reason": reason},
        )


# ══════════════════════════════════════════════════
#  事件总线
# ══════════════════════════════════════════════════

EventHandler = Callable[[BaseEvent], Coroutine[Any, Any, None]]


class EventBus:
    """异步事件总线 — 发布-订阅模式

    特性:
    - 按优先级排序处理（CRITICAL > HIGH > NORMAL > LOW）
    - 支持通配符订阅（"*" 匹配所有事件类型）
    - 内置背压保护（max_queue_size）
    - 统计信息追踪
    """

    def __init__(self, max_queue_size: int = 10000):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._subscribers: Dict[EventType, List[EventHandler]] = {}
        self._wildcard_subscribers: List[EventHandler] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 统计
        self._published_count: int = 0
        self._processed_count: int = 0
        self._dropped_count: int = 0
        self._start_time: float = 0.0

    # ── 订阅管理 ──

    def subscribe(
        self, event_type: EventType, handler: EventHandler
    ) -> None:
        """订阅特定事件类型"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug(f"[EventBus] +订阅 {event_type.value}: {handler.__name__}")

    def subscribe_all(self, handler: EventHandler) -> None:
        """订阅所有事件类型（通配符）"""
        self._wildcard_subscribers.append(handler)
        logger.debug(f"[EventBus] +通配符订阅: {handler.__name__}")

    def unsubscribe(
        self, event_type: EventType, handler: EventHandler
    ) -> None:
        """取消订阅"""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                h for h in self._subscribers[event_type] if h != handler
            ]

    # ── 发布 ──

    async def publish(self, event: BaseEvent) -> bool:
        """发布事件到总线（非阻塞，队列满时丢弃低优先级事件）"""
        try:
            # 优先级取反（asyncio.PriorityQueue 越小越优先）
            prio = -event.priority.value
            self._queue.put_nowait((prio, self._published_count, event))
            self._published_count += 1
            return True
        except asyncio.QueueFull:
            if event.priority == EventPriority.CRITICAL:
                # CRITICAL 事件不可丢弃：等待空间
                await self._queue.put((prio, self._published_count, event))
                self._published_count += 1
                return True
            self._dropped_count += 1
            logger.warning(
                f"[EventBus] 队列满，丢弃 {event.event_type.value}/{event.symbol}"
            )
            return False

    def publish_sync(self, event: BaseEvent) -> bool:
        """同步发布（在非异步上下文中使用）"""
        try:
            prio = -event.priority.value
            self._queue.put_nowait((prio, self._published_count, event))
            self._published_count += 1
            return True
        except asyncio.QueueFull:
            self._dropped_count += 1
            return False

    # ── 运行 ──

    async def start(self) -> None:
        """启动事件处理循环"""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._process_loop())
        logger.info("[EventBus] 事件总线已启动")

    async def stop(self) -> None:
        """停止事件处理循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            f"[EventBus] 事件总线已停止 "
            f"(published={self._published_count} processed={self._processed_count})"
        )

    async def _process_loop(self) -> None:
        """主处理循环"""
        while self._running:
            try:
                prio, seq, event = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                await self._dispatch(event)
                self._processed_count += 1
            except Exception as e:
                logger.error(
                    f"[EventBus] 事件分发异常 {event.event_type.value}: {e}",
                    exc_info=True,
                )
            finally:
                self._queue.task_done()

    async def _dispatch(self, event: BaseEvent) -> None:
        """分发事件到所有订阅者"""
        handlers: List[EventHandler] = list(self._wildcard_subscribers)
        if event.event_type in self._subscribers:
            handlers.extend(self._subscribers[event.event_type])

        if not handlers:
            return

        # 并行通知所有订阅者
        tasks = []
        for handler in handlers:
            try:
                tasks.append(asyncio.create_task(handler(event)))
            except Exception as e:
                logger.debug(f"[EventBus] handler {handler.__name__} 创建失败: {e}")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.debug(
                        f"[EventBus] handler 执行异常: {result}"
                    )

    # ── 统计 ──

    @property
    def stats(self) -> Dict[str, Any]:
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "running": self._running,
            "uptime_seconds": round(uptime, 1),
            "published": self._published_count,
            "processed": self._processed_count,
            "dropped": self._dropped_count,
            "queue_size": self._queue.qsize(),
            "subscriber_count": sum(len(v) for v in self._subscribers.values())
            + len(self._wildcard_subscribers),
            "events_per_second": round(
                self._processed_count / max(uptime, 1), 1
            ),
        }

    def log_stats(self) -> None:
        """记录当前统计信息"""
        s = self.stats
        logger.info(
            f"[EventBus] 统计: published={s['published']} "
            f"processed={s['processed']} dropped={s['dropped']} "
            f"queue={s['queue_size']} subscribers={s['subscriber_count']} "
            f"eps={s['events_per_second']}"
        )

    # ══════════════════════════════════════════════════
    #  Phase 1B: QAA Agent 调用语义 (新增, 不修改现有方法)
    # ══════════════════════════════════════════════════

    def __init_qaa(self):
        """延迟初始化 QAA 组件 (避免 import 循环)"""
        if hasattr(self, "_qaa_initialized"):
            return
        self._qaa_initialized = True
        self._agent_registry: Dict[str, dict] = {}     # agent_id → {card, handler}
        self._circuit_breakers: Dict[str, "CircuitBreaker"] = {}
        self._qaa_audit_log: List[dict] = []
        self._qaa_audit_lock = threading.Lock()

    def register_agent(
        self,
        agent_id: str,
        card: Any,
        handler: Callable[..., Any],
    ) -> None:
        """注册 Agent 到 EventBus (QAA Phase 1B)

        Args:
            agent_id: 唯一标识 (对应 AgentCard.agent_id)
            card: AgentCard 实例 (backend.services.qaa.models.AgentCard)
            handler: 可调用对象, 签名 (action: str, payload: dict) -> Any
        """
        self.__init_qaa()
        self._agent_registry[agent_id] = {"card": card, "handler": handler}
        self._circuit_breakers[agent_id] = CircuitBreaker(
            config=card.circuit_breaker,
            agent_id=agent_id,
        )
        logger.info(
            f"[EventBus][QAA] 注册 Agent: {agent_id} "
            f"(llm={card.llm_level.value}, timeout={card.max_timeout_sec}s)"
        )

    def call_agent_sync(
        self,
        agent_id: str,
        action: str,
        payload: Optional[dict] = None,
        timeout_ms: Optional[float] = None,
        caller_id: str = "",
    ) -> Any:
        """同步调用单个 Agent, 带超时 + 熔断保护 (QAA Phase 1B)

        Args:
            agent_id: 目标 Agent ID
            action: 要调用的能力名称
            payload: 调用参数
            timeout_ms: 超时毫秒 (None=使用 AgentCard 中的默认值)
            caller_id: 调用者 ID (审计用)

        Returns:
            Agent 返回值, 或 fallback 值 (超时/熔断时)
        """
        self.__init_qaa()

        if agent_id not in self._agent_registry:
            logger.error(f"[EventBus][QAA] 未注册 Agent: {agent_id}")
            return None

        reg = self._agent_registry[agent_id]
        card = reg["card"]
        handler = reg["handler"]
        cb = self._circuit_breakers[agent_id]

        # 熔断检查
        cb_state = cb.state
        if cb_state == "open":
            logger.warning(f"[EventBus][QAA] 熔断器 OPEN: {agent_id}, 使用 fallback")
            self._audit(
                agent_id=agent_id, action=action, caller_id=caller_id,
                status="circuit_open", elapsed_ms=0,
            )
            return card.fallback_value

        # 确定超时
        tmo_ms = timeout_ms if timeout_ms is not None else card.max_timeout_sec * 1000
        tmo_s = tmo_ms / 1000.0

        # 执行 (带超时)
        start = time.monotonic()
        result_box = [None]
        error_box = [None]

        def _target():
            try:
                result_box[0] = handler(action, payload or {})
            except Exception as e:
                error_box[0] = e

        t = threading.Thread(target=_target, daemon=True, name=f"qaa-{agent_id[:16]}")
        t.start()
        t.join(timeout=tmo_s)

        elapsed_ms = (time.monotonic() - start) * 1000

        if t.is_alive():
            # 超时
            logger.warning(
                f"[EventBus][QAA] 超时: {agent_id}.{action} "
                f"超过 {tmo_ms:.0f}ms, strategy={card.timeout_strategy}"
            )
            cb.record_failure()
            self._audit(
                agent_id=agent_id, action=action, caller_id=caller_id,
                status="timeout", elapsed_ms=elapsed_ms,
            )
            return card.fallback_value

        if error_box[0] is not None:
            logger.warning(
                f"[EventBus][QAA] 异常: {agent_id}.{action} → "
                f"{type(error_box[0]).__name__}: {error_box[0]}"
            )
            cb.record_failure()
            self._audit(
                agent_id=agent_id, action=action, caller_id=caller_id,
                status="error", elapsed_ms=elapsed_ms,
                error=str(error_box[0]),
            )
            return card.fallback_value

        # 成功
        cb.record_success()
        self._audit(
            agent_id=agent_id, action=action, caller_id=caller_id,
            status="ok", elapsed_ms=elapsed_ms,
        )
        return result_box[0]

    def call_agents_parallel_sync(
        self,
        calls: List[Any],
        global_timeout_ms: Optional[float] = None,
        caller_id: str = "",
    ) -> List[Any]:
        """并行调用多个 Agent, 各自独立超时 (QAA Phase 1B)

        Args:
            calls: AgentCall 列表 (backend.services.qaa.models.AgentCall)
                   或 dict 列表 [{"agent_id": ..., "action": ..., "payload": ...}]
            global_timeout_ms: 全局超时 (None=取所有 call 中最大 timeout)
            caller_id: 调用者 ID

        Returns:
            结果列表, 顺序与 calls 一致, 失败/超时项返回 fallback
        """
        self.__init_qaa()

        if not calls:
            return []

        # 确定全局超时
        if global_timeout_ms is None:
            global_timeout_ms = max(
                (c.timeout_ms if hasattr(c, "timeout_ms") else 30000) for c in calls
            )

        # 使用线程并行执行
        results: List[Any] = [None] * len(calls)
        errors: List[Optional[Exception]] = [None] * len(calls)
        done_flags: List[bool] = [False] * len(calls)

        def _run_one(idx: int, call: Any):
            agent_id = call.agent_id if hasattr(calls[idx], "agent_id") else calls[idx].get("agent_id")
            action = call.action if hasattr(calls[idx], "action") else calls[idx].get("action")
            payload = call.payload if hasattr(calls[idx], "payload") else calls[idx].get("payload", {})
            tmo_ms = call.timeout_ms if hasattr(calls[idx], "timeout_ms") else calls[idx].get("timeout_ms", 30000)
            try:
                results[idx] = self.call_agent_sync(
                    agent_id=agent_id,
                    action=action,
                    payload=payload,
                    timeout_ms=tmo_ms,
                    caller_id=caller_id,
                )
                done_flags[idx] = True
            except Exception as e:
                errors[idx] = e
                done_flags[idx] = True

        threads = []
        for i, call in enumerate(calls):
            t = threading.Thread(target=_run_one, args=(i, call), daemon=True)
            threads.append(t)
            t.start()

        # 等待全部完成或全局超时
        global_tmo_s = global_timeout_ms / 1000.0
        deadline = time.monotonic() + global_tmo_s
        for t in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            t.join(timeout=remaining)

        # 处理未完成的
        for i, call in enumerate(calls):
            if not done_flags[i]:
                agent_id = call.agent_id if hasattr(call, "agent_id") else call.get("agent_id")
                logger.warning(f"[EventBus][QAA] 并行调用全局超时: {agent_id}")
                # 尝试获取 fallback
                if agent_id in self._agent_registry:
                    results[i] = self._agent_registry[agent_id]["card"].fallback_value
            elif errors[i] is not None:
                results[i] = None

        return results

    def _audit(
        self,
        agent_id: str,
        action: str,
        caller_id: str,
        status: str,
        elapsed_ms: float,
        error: str = "",
    ):
        """内部: 记录 QAA 审计日志"""
        entry = {
            "ts": time.time(),
            "agent_id": agent_id,
            "action": action,
            "caller_id": caller_id,
            "status": status,
            "elapsed_ms": round(elapsed_ms, 1),
            "error": error,
        }
        with self._qaa_audit_lock:
            self._qaa_audit_log.append(entry)
            if len(self._qaa_audit_log) > 500:
                self._qaa_audit_log = self._qaa_audit_log[-400:]

    @property
    def qaa_stats(self) -> Dict[str, Any]:
        """QAA Agent 调用统计"""
        self.__init_qaa()
        cb_states = {
            aid: {"state": cb.state, "failures": cb._failure_count}
            for aid, cb in self._circuit_breakers.items()
        }
        return {
            "registered_agents": list(self._agent_registry.keys()),
            "circuit_breakers": cb_states,
            "audit_log_size": len(self._qaa_audit_log),
        }


# ══════════════════════════════════════════════════
#  Phase 1B: CircuitBreaker 熔断器 (独立类)
# ══════════════════════════════════════════════════


class CircuitBreaker:
    """三状态熔断器: CLOSED → OPEN → HALF_OPEN

    借鉴 Martin Fowler Circuit Breaker Pattern + TradingAgents 风控机制。
    线程安全 (threading.Lock)。
    """

    def __init__(self, config: Any = None, agent_id: str = ""):
        if config is None:
            from backend.services.qaa.models import CircuitBreakerConfig
            config = CircuitBreakerConfig()

        self._config = config
        self._agent_id = agent_id
        self._state = "closed"               # closed / open / half_open
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "open":
                # 检查是否可以进入 half_open
                recovery = self._config.recovery_timeout_sec
                if (self._last_failure_time is not None
                        and time.time() - self._last_failure_time >= recovery):
                    self._state = "half_open"
                    self._half_open_calls = 0
            return self._state

    def record_success(self):
        with self._lock:
            if self._state == "half_open":
                self._half_open_calls += 1
                # 半开状态连续成功 → 关闭
                self._success_count += 1
                if self._success_count >= self._config.half_open_max_calls:
                    self._state = "closed"
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == "closed":
                self._failure_count = 0  # 成功重置失败计数

    def record_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == "half_open":
                # 半开失败 → 直接回到 open
                self._state = "open"
                self._success_count = 0
            elif self._state == "closed":
                if self._failure_count >= self._config.failure_threshold:
                    self._state = "open"
                    logger.warning(
                        f"[CircuitBreaker] {self._agent_id} 熔断触发: "
                        f"连续 {self._failure_count} 次失败 → OPEN"
                    )

    def reset(self):
        with self._lock:
            self._state = "closed"
            self._failure_count = 0
            self._success_count = 0


# ══════════════════════════════════════════════════
#  内置订阅者示例
# ══════════════════════════════════════════════════


async def _log_critical_events(event: BaseEvent) -> None:
    """默认 CRITICAL 事件日志记录器"""
    if event.priority == EventPriority.CRITICAL:
        logger.warning(
            f"[EventBus] CRITICAL: {event.event_type.value} "
            f"symbol={event.symbol} source={event.source} "
            f"data={event.data}"
        )


async def _track_risk_events(event: BaseEvent) -> None:
    """默认风险事件追踪器 — 记录到内存滚动窗口"""
    if isinstance(event, RiskEvent):
        _risk_event_window.append({
            "time": event.iso_time,
            "type": event.event_type.value,
            "symbol": event.symbol,
            "data": event.data,
        })
        if len(_risk_event_window) > 100:
            _risk_event_window.pop(0)


_risk_event_window: List[Dict[str, Any]] = []


# 模块级单例
event_bus = EventBus(max_queue_size=5000)
event_bus.subscribe_all(_log_critical_events)
event_bus.subscribe_all(_track_risk_events)


def get_risk_event_window() -> List[Dict[str, Any]]:
    """获取最近 100 条风险事件（供外部查询）"""
    return list(_risk_event_window)
