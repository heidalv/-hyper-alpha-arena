"""Background scheduler that keeps SnapshotStore fresh for primary reads."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from sqlalchemy import text

from backend.database.connection import SessionLocal
from backend.services.market_data_symbol_config import resolve_configured_symbols
from backend.services.snapshot_producer import snapshot_producer


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def _normalize_exchange(exchange: str) -> str:
    exchange_key = (exchange or "").strip().lower()
    if exchange_key == "aster":
        return "asterdex"
    return exchange_key


class SnapshotScheduler:
    """Periodically builds read-optimized snapshots from the market-data DB."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._started_at: float | None = None
        self._last_tick_at: float | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error = ""
        self._captures = 0

    @staticmethod
    def enabled() -> bool:
        return _env_bool("SNAPSHOT_SCHEDULER_ENABLED")

    def config(self) -> dict[str, Any]:
        requested_exchange = os.getenv("SNAPSHOT_PRIMARY_EXCHANGE", "account_selected").strip().lower()
        resolved_exchange, exchange_resolution = self._resolve_primary_exchange(requested_exchange)
        symbols, symbol_resolution = resolve_configured_symbols(
            "SNAPSHOT_SYMBOLS",
            fallback_env_name="SNAPSHOT_FALLBACK_SYMBOLS",
        )
        return {
            "enabled": self.enabled(),
            "snapshot_store_enabled": snapshot_producer.enabled(),
            "interval_seconds": max(30, int(os.getenv("SNAPSHOT_SCHEDULER_INTERVAL", "60"))),
            "exchange": resolved_exchange,
            "requested_exchange": requested_exchange,
            "exchange_resolution": exchange_resolution,
            "symbols": symbols,
            "symbol_resolution": symbol_resolution,
            "periods": _csv("SNAPSHOT_PERIODS", "1m,5m,15m,1h"),
            "count": max(1, min(int(os.getenv("SNAPSHOT_KLINE_COUNT", "120")), 200)),
        }

    def _resolve_primary_exchange(self, requested_exchange: str) -> tuple[str, dict[str, Any]]:
        requested = _normalize_exchange(requested_exchange)
        fallback = _normalize_exchange(os.getenv("SNAPSHOT_FALLBACK_EXCHANGE", "hyperliquid"))
        if requested not in {"", "auto", "configured", "account_selected"}:
            return requested, {"mode": "fixed", "exchange": requested}

        try:
            with SessionLocal() as db:
                rows = db.execute(
                    text("""
                    SELECT
                        s.session_id,
                        s.trading_mode,
                        s.active_exchange,
                        s.account_id,
                        s.paper_account_id,
                        trader.selected_exchange AS trader_exchange,
                        paper.selected_exchange AS paper_exchange
                    FROM full_auto_sessions s
                    LEFT JOIN accounts trader ON trader.id = s.account_id
                    LEFT JOIN accounts paper ON paper.id = s.paper_account_id
                    WHERE s.status = 'running'
                    ORDER BY s.started_at DESC
                    LIMIT 1
                    """)
                ).mappings().all()
        except Exception as exc:
            return fallback, {"mode": "account_selected", "fallback": fallback, "error": f"{type(exc).__name__}: {exc}"}

        if not rows:
            return fallback, {"mode": "account_selected", "fallback": fallback, "reason": "no_running_session"}

        row = dict(rows[0])
        active_exchange = _normalize_exchange(row.get("active_exchange") or "")
        paper_exchange = _normalize_exchange(row.get("paper_exchange") or "")
        trader_exchange = _normalize_exchange(row.get("trader_exchange") or "")
        trading_mode = (row.get("trading_mode") or "paper").strip().lower()
        exchange = active_exchange or (paper_exchange if trading_mode == "paper" else trader_exchange) or trader_exchange or fallback
        return exchange, {
            "mode": "account_selected",
            "exchange": exchange,
            "session_id": row.get("session_id"),
            "trading_mode": trading_mode,
            "active_exchange": active_exchange or None,
            "paper_account_id": row.get("paper_account_id"),
            "paper_exchange": paper_exchange or None,
            "trader_account_id": row.get("account_id"),
            "trader_exchange": trader_exchange or None,
            "fallback": fallback,
        }

    def start(self) -> dict[str, Any]:
        cfg = self.config()
        if not cfg["enabled"]:
            return {"started": False, "reason": "SNAPSHOT_SCHEDULER_ENABLED=false", "status": self.status()}
        if not cfg["snapshot_store_enabled"]:
            return {"started": False, "reason": "SNAPSHOT_STORE_ENABLED=false", "status": self.status()}
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

    async def tick_once(self, *, force: bool = False) -> dict[str, Any]:
        cfg = self.config()
        if not cfg["symbols"]:
            result = {"captured": False, "reason": "no_configured_symbols", "config": cfg}
        elif not force and not cfg["snapshot_store_enabled"]:
            result = {"captured": False, "reason": "SNAPSHOT_STORE_ENABLED=false", "config": cfg}
        else:
            result = await asyncio.to_thread(
                snapshot_producer.capture,
                symbols=cfg["symbols"],
                periods=cfg["periods"],
                exchange=cfg["exchange"],
                count=cfg["count"],
                force=force,
            )
            result["config"] = cfg
        self._last_tick_at = time.time()
        self._last_result = result
        if result.get("captured"):
            self._captures += 1
            self._last_error = ""
        elif result.get("reason"):
            self._last_error = str(result.get("reason"))
        return result

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
            "captures": self._captures,
            "last_error": self._last_error,
            "last_result": self._last_result,
            "config": self.config(),
        }


snapshot_scheduler = SnapshotScheduler()
