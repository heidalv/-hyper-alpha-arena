#!/usr/bin/env python3
"""Background service for repairing finalized crypto_klines.

The service is intentionally disabled by default. It only repairs stable candles
that are older than the configured settlement delay.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from typing import Any

from backend.scripts.kline_quality_repair import run as run_kline_quality_repair
from backend.services.market_data_symbol_config import normalize_symbols, resolve_configured_symbols, symbols_csv


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


class KlineQualityRepairService:
    """Runs conservative post-close K-line finalization checks."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False
        self._started_at: float | None = None
        self._last_run_at: float | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_error = ""
        self._run_count = 0
        # [2026-08-28 剥离] 子进程句柄（修复在独立进程执行）
        self._child: object | None = None
    @staticmethod
    def enabled() -> bool:
        return _env_bool("KLINE_QUALITY_REPAIR_ENABLED")

    @staticmethod
    def apply_enabled() -> bool:
        return _env_bool("KLINE_QUALITY_REPAIR_APPLY_ENABLED")

    def config(self) -> dict[str, Any]:
        symbols, symbol_resolution = resolve_configured_symbols(
            "KLINE_QUALITY_REPAIR_SYMBOLS",
            fallback_env_name="KLINE_QUALITY_REPAIR_FALLBACK_SYMBOLS",
        )
        return {
            "enabled": self.enabled(),
            "apply_enabled": self.apply_enabled(),
            "exchange": os.getenv("KLINE_QUALITY_REPAIR_EXCHANGE", "hyperliquid").strip().lower(),
            "exchanges": _csv("KLINE_QUALITY_REPAIR_EXCHANGES", os.getenv("KLINE_QUALITY_REPAIR_EXCHANGE", "hyperliquid")),
            "symbols": symbols_csv(symbols),
            "symbol_resolution": symbol_resolution,
            "periods": _csv("KLINE_QUALITY_REPAIR_PERIODS", "1m,5m,15m,1h"),
            "limit": max(1, min(int(os.getenv("KLINE_QUALITY_REPAIR_LIMIT", "240")), 5000)),
            "interval_seconds": max(60, int(os.getenv("KLINE_QUALITY_REPAIR_INTERVAL", "900"))),
            "settle_periods": max(0, int(os.getenv("KLINE_QUALITY_REPAIR_SETTLE_PERIODS", "3"))),
            "settle_seconds": max(0, int(os.getenv("KLINE_QUALITY_REPAIR_SETTLE_SECONDS", "3600"))),
            "fetch_timeout": max(5, int(os.getenv("KLINE_QUALITY_REPAIR_FETCH_TIMEOUT", "30"))),
            "volume_tolerance": float(os.getenv("KLINE_QUALITY_REPAIR_VOLUME_TOLERANCE", "0.01")),
        }

    async def tick_once(
        self,
        *,
        apply: bool = False,
        symbols: str | None = None,
        periods: str | None = None,
        limit: int | None = None,
        exchange: str | None = None,
        exchanges: str | None = None,
    ) -> dict[str, Any]:
        cfg = self.config()
        should_apply = bool(apply and self.apply_enabled())
        # [2026-08-28 剥离] 防重叠：上一轮子进程未结束则跳过本轮
        _ch = getattr(self, "_child", None)
        if _ch is not None and _ch.poll() is None:
            return {
                "ok": False,
                "requested_apply": apply,
                "applied": False,
                "elapsed_seconds": 0,
                "skipped": True,
                "reason": "child_process_running pid=%s" % _ch.pid,
                "config": cfg,
            }
        requested_symbols = symbols if symbols is not None else cfg["symbols"]
        if not normalize_symbols(requested_symbols):
            return {
                "ok": True,
                "requested_apply": apply,
                "applied": False,
                "elapsed_seconds": 0,
                "skipped": True,
                "reason": "no_configured_symbols",
                "config": cfg,
            }
        args = argparse.Namespace(
            exchange=exchange or cfg["exchange"],
            exchanges=exchanges or cfg["exchanges"],
            symbol="BTC",
            period="1m",
            symbols=requested_symbols,
            periods=periods or cfg["periods"],
            limit=limit or cfg["limit"],
            volume_tolerance=cfg["volume_tolerance"],
            closed_only=True,
            settle_periods=cfg["settle_periods"],
            settle_seconds=cfg["settle_seconds"],
            fetch_timeout=cfg["fetch_timeout"],
            apply=should_apply,
        )

        started = time.time()
        try:
            # [2026-08-28 剥离] 重活移出主进程：独立子进程（独立 GIL/DB/采集），
            # 主进程只做调度与日志，修复期间 /api 不再被拖累。
            import subprocess as _sp
            import sys as _sys

            _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            _py = os.path.join(_repo, ".venv", "Scripts", "python.exe")
            if not os.path.exists(_py):
                _py = _sys.executable
            _argv = [
                _py, "-m", "backend.scripts.kline_quality_repair",
                "--exchanges", ",".join(str(x) for x in args.exchanges if x),
                "--symbols", ",".join(str(x) for x in (args.symbols or [])),
                "--periods", ",".join(str(x) for x in (args.periods or [])),
                "--limit", str(args.limit),
                "--fetch-timeout", str(args.fetch_timeout),
                "--volume-tolerance", str(args.volume_tolerance),
                "--settle-periods", str(args.settle_periods),
                "--settle-seconds", str(args.settle_seconds),
            ]
            if not args.closed_only:
                _argv.append("--include-open")
            if should_apply:
                _argv.append("--apply")
            _lf = open(os.path.join(_repo, "logs", "kline_repair_%d.log" % int(time.time())), "w", encoding="utf-8")
            self._child = _sp.Popen(
                _argv, cwd=_repo,
                stdout=_lf, stderr=_sp.STDOUT,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
            )
            result = {
                "spawned": True,
                "pid": self._child.pid,
                "log_file": _lf.name,
                "child_running": True,
                "note": "repair running in child process (独立进程)",
            }
            self._last_result = result
            self._last_error = ""
            self._last_run_at = time.time()
            self._run_count += 1
            return {
                "ok": True,
                "requested_apply": apply,
                "applied": should_apply,
                "blocked_reason": "" if should_apply or not apply else "KLINE_QUALITY_REPAIR_APPLY_ENABLED=false",
                "elapsed_seconds": round(time.time() - started, 2),
                "result": result,
            }
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._last_run_at = time.time()
            return {
                "ok": False,
                "requested_apply": apply,
                "applied": False,
                "elapsed_seconds": round(time.time() - started, 2),
                "error": self._last_error,
            }

    def start(self) -> dict[str, Any]:
        if not self.enabled():
            return {"started": False, "reason": "KLINE_QUALITY_REPAIR_ENABLED=false", "status": self.status()}
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

    async def _loop(self) -> None:
        while self._running:
            cfg = self.config()
            await self.tick_once(apply=self.apply_enabled())
            await asyncio.sleep(cfg["interval_seconds"])

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        return {
            "enabled": cfg["enabled"],
            "apply_enabled": cfg["apply_enabled"],
            "running": self._running and self._task is not None and not self._task.done(),
            "started_at": self._started_at,
            "last_run_at": self._last_run_at,
            "run_count": self._run_count,
            "last_error": self._last_error,
            "config": cfg,
            "child_pid": getattr(self, "_child", None).pid if getattr(self, "_child", None) and getattr(self, "_child", None).poll() is None else None,
            "last_result_summary": self._summarize_result(self._last_result),
        }

    @staticmethod
    def _summarize_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not result:
            return None
        aggregate = result.get("aggregate")
        if aggregate:
            return {
                "checks": aggregate.get("checks"),
                "failed": aggregate.get("failed"),
                "no_data": aggregate.get("no_data"),
                "closed": aggregate.get("closed"),
                "existing": aggregate.get("existing"),
                "mismatch_count": aggregate.get("mismatch_count"),
                "apply_result": aggregate.get("apply_result"),
            }
        return {
            "symbol": result.get("symbol"),
            "period": result.get("period"),
            "closed": result.get("closed"),
            "existing": result.get("existing"),
            "mismatch_count": result.get("mismatch_count"),
            "apply_result": result.get("apply_result"),
        }


kline_quality_repair_service = KlineQualityRepairService()
