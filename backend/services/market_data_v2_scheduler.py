"""Optional scheduler for market-data v2 shadow ingest."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from backend.services.market_data_ingest_queue import IngestTask, market_data_ingest_queue
from backend.services.market_data_symbol_config import resolve_configured_symbols


class MarketDataV2Scheduler:
    """Periodic task submitter for the v2 ingest queue, disabled by default."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._started_at: float | None = None
        self._last_tick_at: float | None = None
        self._submitted = 0
        self._last_error = ""

    @staticmethod
    def enabled() -> bool:
        # 根因3修复：数据中台 V2 调度器默认开。
        return os.getenv("MARKET_DATA_V2_SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

    def config(self) -> dict[str, Any]:
        symbols, symbol_resolution = resolve_configured_symbols(
            "MARKET_DATA_V2_SYMBOLS",
            fallback_env_name="MARKET_DATA_V2_FALLBACK_SYMBOLS",
        )
        timeframes = [
            p.strip()
            for p in os.getenv("MARKET_DATA_V2_TIMEFRAMES", "1m").split(",")
            if p.strip()
        ]
        exchanges = [
            e.strip().lower()
            for e in os.getenv("MARKET_DATA_V2_EXCHANGES", "hyperliquid").split(",")
            if e.strip()
        ]
        return {
            "enabled": self.enabled(),
            "interval_seconds": max(10, int(os.getenv("MARKET_DATA_V2_SCHEDULER_INTERVAL", "60"))),
            "limit": max(1, min(int(os.getenv("MARKET_DATA_V2_LIMIT", "10")), 500)),
            "symbols": symbols,
            "symbol_resolution": symbol_resolution,
            "timeframes": timeframes,
            "exchanges": exchanges,
        }

    def start(self) -> dict[str, Any]:
        if not self.enabled():
            return {"started": False, "reason": "MARKET_DATA_V2_SCHEDULER_ENABLED=false", "status": self.status()}
        if self._task and not self._task.done():
            return {"started": False, "reason": "already_running", "status": self.status()}
        self._running = True
        self._started_at = time.time()
        self._task = asyncio.create_task(self._loop())
        return {"started": True, "status": self.status()}

    async def stop(self) -> dict[str, Any]:
        self._running = False
        if self._task:
            await self._task
        return {"stopped": True, "status": self.status()}

    async def tick_once(self) -> dict[str, Any]:
        cfg = self.config()
        submitted = 0
        for exchange in cfg["exchanges"]:
            for symbol in cfg["symbols"]:
                for timeframe in cfg["timeframes"]:
                    task = IngestTask(
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=cfg["limit"],
                    )
                    await market_data_ingest_queue.submit(task)
                    submitted += 1
        self._submitted += submitted
        self._last_tick_at = time.time()
        return {"submitted": submitted, "config": cfg}

    async def _loop(self) -> None:
        while self._running:
            cfg = self.config()
            try:
                await self.tick_once()
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(cfg["interval_seconds"])

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled(),
            "running": self._running and self._task is not None and not self._task.done(),
            "started_at": self._started_at,
            "last_tick_at": self._last_tick_at,
            "submitted": self._submitted,
            "last_error": self._last_error,
            "config": self.config(),
        }


market_data_v2_scheduler = MarketDataV2Scheduler()
