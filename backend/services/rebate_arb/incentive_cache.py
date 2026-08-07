"""
IncentiveCache — TTL-based thread-safe cache for exchange incentive data.

Avoids hitting exchange APIs on every tick by caching results with configurable TTLs.
Each key has independent expiry. Thread-safe for use in tick loops.

Usage:
    from backend.services.rebate_arb.incentive_cache import incentive_cache
    cached = incentive_cache.get("binance_fee_tier")
    if cached is None:
        data = await fetch_from_api()
        incentive_cache.set("binance_fee_tier", data, ttl_seconds=3600)
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float
    set_at: float


class IncentiveCache:
    """TTL-based thread-safe cache for exchange incentive data."""

    def __init__(self):
        self._store: Dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get cached value. Returns None if missing or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() > entry.expires_at:
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    def get_or_stale(self, key: str) -> Optional[Any]:
        """Get cached value even if expired (for graceful degradation)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store value with TTL."""
        now = time.time()
        with self._lock:
            self._store[key] = _CacheEntry(
                value=value,
                expires_at=now + ttl_seconds,
                set_at=now,
            )

    def is_stale(self, key: str) -> bool:
        """Check if a key is missing or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return True
            return time.time() > entry.expires_at

    def invalidate(self, key: str) -> None:
        """Remove a key from cache."""
        with self._lock:
            self._store.pop(key, None)

    def invalidate_exchange(self, exchange: str) -> None:
        """Remove all cached data for a specific exchange."""
        with self._lock:
            keys_to_remove = [k for k in self._store if k.startswith(f"{exchange}_")]
            for k in keys_to_remove:
                del self._store[k]

    def get_staleness_report(self) -> Dict[str, Dict[str, Any]]:
        """Get per-key staleness info for monitoring."""
        now = time.time()
        report = {}
        with self._lock:
            for key, entry in self._store.items():
                age = now - entry.set_at
                is_expired = now > entry.expires_at
                report[key] = {
                    "age_seconds": round(age, 1),
                    "is_stale": is_expired,
                    "expires_in": round(entry.expires_at - now, 1) if not is_expired else 0,
                }
        return report

    def get_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / max(total, 1),
                "entries": len(self._store),
                "stale_entries": sum(
                    1 for e in self._store.values() if time.time() > e.expires_at
                ),
            }

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0


# Singleton
incentive_cache = IncentiveCache()
