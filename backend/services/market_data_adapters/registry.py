"""
Exchange adapter registry for the high-throughput market-data path.

This is a thin layer over the existing exchange adapter system. It gives the
new ingest path a stable dependency without replacing trading execution code.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from backend.services.exchange.base_exchange_client import BaseExchangeClient
from backend.services.exchange.exchange_factory import ExchangeClientFactory
from backend.services.symbol_registry import symbol_registry

logger = __import__("logging").getLogger(__name__)


@dataclass
class AdapterStatus:
    exchange: str
    registered: bool
    cached: bool
    supports_klines: bool
    last_used_at: float | None = None
    error: str | None = None


class ExchangeAdapterRegistry:
    """Cached adapter registry for market-data ingestion."""

    def __init__(self) -> None:
        self._clients: Dict[str, BaseExchangeClient] = {}
        self._last_used: Dict[str, float] = {}
        self._errors: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    def registered_exchanges(self) -> list[str]:
        return ExchangeClientFactory.get_registered_exchanges()

    @staticmethod
    def normalize_exchange(exchange: str) -> str:
        exchange_key = exchange.strip().lower()
        if exchange_key == "aster":
            return "asterdex"
        return exchange_key

    @staticmethod
    def market_data_proxy() -> str:
        explicit = (
            os.getenv("MARKET_DATA_HTTP_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("ALL_PROXY")
        )
        if explicit:
            return explicit
        try:
            with socket.create_connection(("127.0.0.1", 7897), timeout=0.2):
                return "http://127.0.0.1:7897"
        except OSError:
            return ""

    async def get_client(self, exchange: str, **kwargs: Any) -> BaseExchangeClient:
        exchange_key = self.normalize_exchange(exchange)
        async with self._lock:
            cached = self._clients.get(exchange_key)
            if cached is not None:
                self._last_used[exchange_key] = time.time()
                return cached

            client = ExchangeClientFactory.create(exchange_key, **kwargs)
            self._clients[exchange_key] = client
            self._last_used[exchange_key] = time.time()
            self._errors.pop(exchange_key, None)
            return client

    async def get_klines(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        limit: int = 100,
        market_type: str = "perp",
    ) -> list[dict[str, Any]]:
        exchange_key = self.normalize_exchange(exchange)
        # [2026-08-04 统一数据源] DC_ONLY 模式：所有 K 线读取统一走数据中心落库，
        # 禁止经本 registry 直连交易所（含 v2 影子采集与 market_data 兜底路径）。
        # 数据中心采集进程是唯一允许直连交易所的组件（kline_realtime_collector 等）。
        if os.getenv("MARKET_DATA_DC_ONLY", "true").strip().lower() not in (
            "0", "false", "no", "off",
        ):
            try:
                from backend.services.data_center import data_center
                result = data_center.get_klines(
                    symbol, interval, count=limit, exchange=exchange_key, purpose="research",
                )
                if result.rows:
                    out = []
                    for row in result.rows:
                        try:
                            out.append({
                                "timestamp": int(row.get("timestamp") or 0),
                                "open": float(row.get("open") or 0),
                                "high": float(row.get("high") or 0),
                                "low": float(row.get("low") or 0),
                                "close": float(row.get("close") or 0),
                                "volume": float(row.get("volume") or 0),
                            })
                        except (TypeError, ValueError):
                            continue
                    if out:
                        self._last_used[exchange_key] = time.time()
                        self._errors.pop(exchange_key, None)
                        return out
            except Exception as exc:
                logger.warning(f"[DC_ONLY] registry kline DB fallback failed: {exc}")
            # 数据中心无数据：DC_ONLY 下不再直连交易所，返回空（调用方自行处理）
            return []

        mapping = symbol_registry.resolve(exchange_key, symbol, market_type=market_type)
        if exchange_key == "hyperliquid":
            try:
                from backend.services.hyperliquid_market_data import get_kline_data_from_hyperliquid
                data = await asyncio.to_thread(
                    get_kline_data_from_hyperliquid,
                    mapping.canonical_symbol,
                    interval,
                    limit,
                    False,
                    "mainnet",
                )
                self._last_used[exchange_key] = time.time()
                self._errors.pop(exchange_key, None)
                return data or []
            except Exception as exc:
                self._errors[exchange_key] = f"{type(exc).__name__}: {exc}"
                raise

        if exchange_key in {"binance", "asterdex"}:
            try:
                data = await asyncio.to_thread(
                    self._get_binance_compatible_futures_klines,
                    exchange_key,
                    mapping.canonical_symbol,
                    mapping.quote_asset,
                    interval,
                    limit,
                )
                self._last_used[exchange_key] = time.time()
                self._errors.pop(exchange_key, None)
                return data
            except Exception as exc:
                self._errors[exchange_key] = f"{type(exc).__name__}: {exc}"
                raise

        client = await self.get_client(exchange_key)
        try:
            data = await client.get_klines(mapping.exchange_symbol, interval, limit=limit)
            self._last_used[exchange_key] = time.time()
            self._errors.pop(exchange_key, None)
            return data or []
        except Exception as exc:
            self._errors[exchange_key] = f"{type(exc).__name__}: {exc}"
            raise

    def status(self, exchange: Optional[str] = None) -> dict[str, AdapterStatus]:
        exchanges = [self.normalize_exchange(exchange)] if exchange else self.registered_exchanges()
        result: dict[str, AdapterStatus] = {}
        for ex in sorted(set(exchanges)):
            client = self._clients.get(ex)
            result[ex] = AdapterStatus(
                exchange=ex,
                registered=ExchangeClientFactory.is_registered(ex),
                cached=client is not None,
                supports_klines=bool(client and hasattr(client, "get_klines")),
                last_used_at=self._last_used.get(ex),
                error=self._errors.get(ex),
            )
        return result

    def clear(self) -> None:
        self._clients.clear()
        self._last_used.clear()
        self._errors.clear()

    async def close_all(self) -> None:
        """Release cached async exchange clients."""
        clients = list(self._clients.values())
        for client in clients:
            close = getattr(client, "close", None)
            if close is None:
                continue
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
        self.clear()

    @staticmethod
    def _get_binance_compatible_futures_klines(
        exchange: str,
        canonical_symbol: str,
        quote_asset: str,
        interval: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        base_urls = (
            [
                "https://fapi.binance.com",
                "https://fapi1.binance.com",
                "https://fapi2.binance.com",
                "https://fapi3.binance.com",
                "https://fapi4.binance.com",
            ]
            if exchange == "binance"
            else ["https://fapi.asterdex.com"]
        )
        symbol = f"{canonical_symbol.upper()}{quote_asset.upper()}"
        query = urlencode({"symbol": symbol, "interval": interval, "limit": max(1, min(limit, 1500))})
        last_error: Exception | None = None
        raw = []
        proxy = ExchangeAdapterRegistry.market_data_proxy()
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy})) if proxy else None
        for base_url in base_urls:
            request = Request(
                f"{base_url}/fapi/v1/klines?{query}",
                headers={"User-Agent": "HyperAlphaArena/market-data-v2"},
            )
            try:
                open_func = opener.open if opener else urlopen
                with open_func(request, timeout=8) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:
                last_error = exc
                raw = []
        if not raw and last_error is not None:
            raise last_error
        result = []
        for candle in raw if isinstance(raw, list) else []:
            if not isinstance(candle, list) or len(candle) < 6:
                continue
            result.append({
                "timestamp": int(candle[0]) // 1000,
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]) if candle[5] is not None else None,
            })
        return result


exchange_adapter_registry = ExchangeAdapterRegistry()
