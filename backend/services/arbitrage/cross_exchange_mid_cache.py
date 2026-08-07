"""
CrossExchangeMidCache — 跨交易所 mid price 缓存

Phase 3: 降低 tick 内 REST 拉取频率，为跨所套利提供带 TTL 的统一 mid 视图。
后续可替换为 WebSocket 推送写入同一缓存接口。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class MidPriceEntry:
    exchange: str
    symbol: str
    mid_price: float
    bid: float
    ask: float
    updated_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.updated_at)

    def is_stale(self, ttl: float) -> bool:
        return self.age_seconds > ttl


class CrossExchangeMidCache:
    """线程安全的 {exchange, symbol} → mid 缓存"""

    DEFAULT_TTL_SEC = 5.0

    def __init__(self, ttl_sec: float = DEFAULT_TTL_SEC):
        self._ttl = ttl_sec
        self._data: Dict[Tuple[str, str], MidPriceEntry] = {}
        self._lock = threading.Lock()
        self._refresh_count = 0
        self._last_refresh_ts = 0.0

    def get_mid(self, exchange: str, symbol: str) -> Optional[MidPriceEntry]:
        key = (exchange.lower(), symbol)
        with self._lock:
            entry = self._data.get(key)
            if entry is None or entry.is_stale(self._ttl):
                return None
            return entry

    def set_mid(
        self,
        exchange: str,
        symbol: str,
        mid_price: float,
        bid: float = 0.0,
        ask: float = 0.0,
    ) -> None:
        key = (exchange.lower(), symbol)
        with self._lock:
            self._data[key] = MidPriceEntry(
                exchange=exchange.lower(),
                symbol=symbol,
                mid_price=mid_price,
                bid=bid or mid_price,
                ask=ask or mid_price,
                updated_at=time.time(),
            )

    def refresh_from_orderbook(
        self,
        exchange: str,
        symbol: str,
        book: Dict[str, Any],
    ) -> Optional[float]:
        """从 orderbook dict 解析 mid 并写入缓存"""
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        mid = (bid + ask) / 2
        if mid <= 0:
            return None
        self.set_mid(exchange, symbol, mid, bid, ask)
        return mid

    async def refresh_symbols(
        self,
        exchange_manager: Any,
        symbols: List[str],
    ) -> Dict[str, Any]:
        """并发刷新所有已连接交易所的 mid 缓存"""
        clients = exchange_manager.get_all_clients() if exchange_manager else {}
        if not isinstance(clients, dict):
            return {"updated": 0, "errors": 0}

        updated = 0
        errors = 0
        for key, client in clients.items():
            ex = getattr(getattr(client, "exchange_type", None), "value", key)
            for symbol in symbols:
                try:
                    book = await client.get_orderbook(symbol, depth=5)
                    if self.refresh_from_orderbook(ex, symbol, book):
                        updated += 1
                except Exception as e:
                    errors += 1
                    logger.debug("[MidCache] refresh %s/%s: %s", ex, symbol, e)

        with self._lock:
            self._refresh_count += 1
            self._last_refresh_ts = time.time()

        return {
            "updated": updated,
            "errors": errors,
            "exchanges": len(clients),
            "symbols": len(symbols),
        }

    def mark_ws_source(self, exchange: str, symbol: str) -> None:
        """标记该条目来自 WebSocket 推送"""
        key = (exchange.lower(), symbol)
        with self._lock:
            entry = self._data.get(key)
            if entry is not None:
                entry.updated_at = time.time()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            entries = []
            stale_count = 0
            for (ex, sym), entry in self._data.items():
                age = now - entry.updated_at
                is_stale = age > self._ttl
                if is_stale:
                    stale_count += 1
                entries.append({
                    "exchange": ex,
                    "symbol": sym,
                    "mid_price": entry.mid_price,
                    "age_seconds": round(age, 2),
                    "stale": is_stale,
                })
            return {
                "ttl_sec": self._ttl,
                "total_entries": len(self._data),
                "stale_entries": stale_count,
                "refresh_count": self._refresh_count,
                "last_refresh_ts": self._last_refresh_ts,
                "entries": sorted(entries, key=lambda x: (x["exchange"], x["symbol"]))[:100],
            }


# 模块级单例
mid_cache = CrossExchangeMidCache()
