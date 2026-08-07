"""
In-memory ingest queue for market-data v2 shadow collection.

The queue is disabled by default. When enabled, it writes raw events only and
does not replace existing crypto_klines readers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from backend.services.market_data_adapters.registry import exchange_adapter_registry
from backend.services.market_data_metrics import market_data_metrics
from backend.services.raw_market_event_store import raw_market_event_store
from backend.services.symbol_registry import symbol_registry

TaskStatus = Literal["pending", "running", "completed", "failed", "skipped"]

logger = logging.getLogger(__name__)


@dataclass
class IngestTask:
    exchange: str
    symbol: str
    data_type: str = "kline"
    market_type: str = "perp"
    timeframe: str = "1m"
    limit: int = 100
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    raw_events: int = 0
    error: str = ""


class MarketDataIngestQueue:
    """Small async queue for v2 shadow ingest tasks."""

    _CIRCUIT_COOLDOWN_SEC = 60.0

    def __init__(self) -> None:
        self._queue: asyncio.Queue[IngestTask] = asyncio.Queue()
        self._tasks: Dict[str, IngestTask] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._circuit_open_until: Dict[str, float] = {}

    @staticmethod
    def enabled() -> bool:
        # 根因3修复：数据中台 V2 默认开（不再静默禁用）。
        return os.getenv("MARKET_DATA_V2_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

    async def submit(self, task: IngestTask) -> IngestTask:
        if not self.enabled():
            task.status = "skipped"
            task.error = "MARKET_DATA_V2_ENABLED=false"
            self._tasks[task.task_id] = task
            return task

        self._tasks[task.task_id] = task
        await self._queue.put(task)
        self.start_worker()
        return task

    def start_worker(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop_worker(self) -> None:
        self._running = False
        if self._worker_task:
            await self._worker_task

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if self._queue.empty():
                    self._running = False
                continue

            try:
                await self.process_task(task)
            except Exception as exc:
                logger.warning(
                    "[MarketDataIngestQueue] task failed exchange=%s symbol=%s: %s",
                    getattr(task, "exchange", "?"),
                    getattr(task, "symbol", "?"),
                    exc,
                )
            finally:
                self._queue.task_done()

    async def process_task(self, task: IngestTask) -> IngestTask:
        task.status = "running"
        task.started_at = time.time()
        circuit_key = f"{task.exchange}:{task.symbol}".lower()
        open_until = self._circuit_open_until.get(circuit_key, 0.0)
        if open_until > time.time():
            task.status = "skipped"
            task.error = f"circuit_open_{int(open_until - time.time())}s"
            task.finished_at = time.time()
            return task
        with market_data_metrics.timer("market_data_v2.ingest_task"):
            try:
                if task.data_type != "kline":
                    raise ValueError(f"unsupported data_type: {task.data_type}")

                mapping = symbol_registry.resolve(task.exchange, task.symbol, task.market_type)
                klines = await exchange_adapter_registry.get_klines(
                    task.exchange,
                    task.symbol,
                    task.timeframe,
                    limit=task.limit,
                    market_type=task.market_type,
                )

                events = []
                for kline in klines:
                    raw_ts = int(kline.get("timestamp") or kline.get("time") or 0)
                    event_ts = raw_ts // 1000 if raw_ts > 10_000_000_000 else raw_ts
                    if event_ts <= 0:
                        continue
                    events.append({
                        "exchange": mapping.exchange,
                        "market_type": mapping.market_type,
                        "data_type": "kline",
                        "canonical_symbol": mapping.canonical_symbol,
                        "exchange_symbol": mapping.exchange_symbol,
                        "timeframe": task.timeframe,
                        "event_ts": event_ts,
                        "payload": kline,
                    })

                # [2026-07-16 修复事件循环冻结] append_many 是同步阻塞的批量 DB 写入，
                # 原直接在 async process_task（事件循环线程）里调用，每次批量 INSERT 期间
                # 整个事件循环冻结，导致所有 HTTP/WS 请求排队超时（间歇性 500 / ws failed）。
                # 改为丢到线程池执行，事件循环可继续处理请求。
                await asyncio.to_thread(raw_market_event_store.append_many, events)
                task.raw_events = len(events)
                task.status = "completed"
            except Exception as exc:
                task.status = "failed"
                task.error = f"{type(exc).__name__}: {exc}"
                err_s = str(exc).lower()
                if any(
                    token in err_s
                    for token in ("timeout", "10054", "connection", "429", "urlopen")
                ):
                    self._circuit_open_until[circuit_key] = (
                        time.time() + self._CIRCUIT_COOLDOWN_SEC
                    )
                raise
            finally:
                task.finished_at = time.time()
        return task

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            counts[task.status] = counts.get(task.status, 0) + 1
        return {
            "enabled": self.enabled(),
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "total_tasks": len(self._tasks),
            "counts": counts,
            "recent_tasks": [
                {
                    "task_id": task.task_id,
                    "exchange": task.exchange,
                    "symbol": task.symbol,
                    "data_type": task.data_type,
                    "timeframe": task.timeframe,
                    "status": task.status,
                    "raw_events": task.raw_events,
                    "error": task.error,
                }
                for task in sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:20]
            ],
        }


market_data_ingest_queue = MarketDataIngestQueue()
