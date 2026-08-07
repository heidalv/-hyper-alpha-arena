"""
Lightweight market-data throughput metrics.

This module is intentionally in-memory and dependency-free. It gives us a
low-risk Phase 1 visibility layer before introducing queues or batch writers.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)


@dataclass
class MetricBucket:
    name: str
    count: int = 0
    success: int = 0
    failed: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float | None = None
    last_ok: bool | None = None
    last_error: str | None = None
    last_at: str | None = None
    samples_ms: list[float] = field(default_factory=list)

    def observe(self, elapsed_ms: float, ok: bool, error: str | None = None) -> None:
        self.count += 1
        if ok:
            self.success += 1
        else:
            self.failed += 1

        self.total_ms += elapsed_ms
        self.max_ms = max(self.max_ms, elapsed_ms)
        self.last_ms = elapsed_ms
        self.last_ok = ok
        self.last_error = error
        self.last_at = _utc_now_iso()
        self.samples_ms.append(elapsed_ms)
        if len(self.samples_ms) > 500:
            self.samples_ms = self.samples_ms[-500:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "success": self.success,
            "failed": self.failed,
            "success_rate": round(self.success / self.count, 4) if self.count else None,
            "avg_ms": round(self.total_ms / self.count, 2) if self.count else None,
            "p50_ms": _percentile(self.samples_ms, 50),
            "p95_ms": _percentile(self.samples_ms, 95),
            "max_ms": round(self.max_ms, 2),
            "last_ms": round(self.last_ms, 2) if self.last_ms is not None else None,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "last_at": self.last_at,
        }


class MarketDataMetrics:
    """Thread-safe in-memory metrics registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = _utc_now_iso()
        self._buckets: dict[str, MetricBucket] = {}

    def observe(self, name: str, elapsed_ms: float, ok: bool = True, error: str | None = None) -> None:
        with self._lock:
            bucket = self._buckets.get(name)
            if bucket is None:
                bucket = MetricBucket(name=name)
                self._buckets[name] = bucket
            bucket.observe(elapsed_ms=elapsed_ms, ok=ok, error=error)

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        ok = True
        error = None
        try:
            yield
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.observe(name=name, elapsed_ms=elapsed_ms, ok=ok, error=error)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            metrics = {name: bucket.to_dict() for name, bucket in sorted(self._buckets.items())}

        total_count = sum(item["count"] for item in metrics.values())
        total_failed = sum(item["failed"] for item in metrics.values())
        return {
            "started_at": self._started_at,
            "generated_at": _utc_now_iso(),
            "total_count": total_count,
            "total_failed": total_failed,
            "overall_success_rate": round((total_count - total_failed) / total_count, 4) if total_count else None,
            "metrics": metrics,
        }

    def reset(self) -> None:
        with self._lock:
            self._started_at = _utc_now_iso()
            self._buckets.clear()


market_data_metrics = MarketDataMetrics()
