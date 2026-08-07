"""
CrossExchangeWsFeed — 跨所 mid price WebSocket/推送订阅层

数据源：
1. Hyperliquid — 复用 MarketFlowCollector L2 WebSocket（实时推送写入 mid_cache）
2. CCXT 交易所 — watch_order_book（可用时）或 2s REST 轮询降级

与 cross_exchange_mid_cache 共用写入接口，CrossExchangeArbitrageEngine 无感切换。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def push_hyperliquid_l2book(symbol: str, data: Dict[str, Any]) -> None:
    """从 Hyperliquid L2 WebSocket 消息写入 mid 缓存 + MarketDataHub"""
    try:
        levels = data.get("levels", [[], []])
        bids_raw = levels[0] if len(levels) > 0 else []
        asks_raw = levels[1] if len(levels) > 1 else []
        if not bids_raw or not asks_raw:
            return

        bids = [[float(b["px"]), float(b["sz"])] for b in bids_raw[:5]]
        asks = [[float(a["px"]), float(a["sz"])] for a in asks_raw[:5]]
        book = {"bids": bids, "asks": asks}

        try:
            from backend.services.market_data_hub import market_data_hub
            market_data_hub.publish_l2_book("hyperliquid", symbol, book, source="ws")
        except Exception:
            from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache
            mid_cache.refresh_from_orderbook("hyperliquid", symbol, book)
            mid_cache.mark_ws_source("hyperliquid", symbol)
    except Exception as e:
        logger.debug("[WsFeed] HL push %s: %s", symbol, e)


class CrossExchangeWsFeed:
    """后台跨所 mid 订阅服务（单例）"""

    DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL"]
    POLL_INTERVAL_SEC = 2.0

    _instance: Optional["CrossExchangeWsFeed"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._running = False
        self._symbols: List[str] = list(self.DEFAULT_SYMBOLS)
        self._thread: Optional[threading.Thread] = None
        self._ws_sources: Set[str] = set()
        self._poll_updates = 0
        self._watch_updates = 0
        self._last_error = ""
        self._started_at = 0.0

    @classmethod
    def get_instance(cls) -> "CrossExchangeWsFeed":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def is_running(self) -> bool:
        return self._running

    def configure(self, symbols: Optional[List[str]] = None) -> None:
        if symbols:
            self._symbols = list(dict.fromkeys(symbols))

    def start(self, symbols: Optional[List[str]] = None) -> bool:
        if self._running:
            return True
        self.configure(symbols)
        self._load_config()
        self._running = True
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="cross-exchange-ws-feed",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[WsFeed] 启动跨所 mid 订阅 symbols=%s", self._symbols
        )
        return True

    def stop(self) -> None:
        self._running = False
        logger.info("[WsFeed] 已停止")

    def _load_config(self) -> None:
        try:
            from backend.config.arb_config_loader import arb_config
            ws = getattr(arb_config, "ws_feed", None)
            if ws and getattr(ws, "symbols", None):
                self._symbols = list(ws.symbols)
            if ws and getattr(ws, "poll_interval_sec", None):
                self.POLL_INTERVAL_SEC = float(ws.poll_interval_sec)
        except Exception:
            pass
        try:
            from backend.config.arb_config_loader import arb_config
            ttl = arb_config.scanner.mid_cache_ttl_sec
            from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache
            mid_cache._ttl = ttl
        except Exception:
            pass

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main())
        except Exception as e:
            self._last_error = str(e)
            logger.error("[WsFeed] 主循环异常: %s", e)
        finally:
            loop.close()
            self._running = False

    async def _async_main(self) -> None:
        from backend.services.exchange.exchange_manager import get_exchange_manager

        mgr = get_exchange_manager()
        clients = mgr.get_all_clients() if mgr else {}
        if not isinstance(clients, dict):
            clients = {}

        tasks = []
        for key, client in clients.items():
            ex = getattr(getattr(client, "exchange_type", None), "value", key)
            if ex == "hyperliquid":
                continue  # HL 由 MarketFlowCollector WS 推送
            raw = getattr(client, "_exchange", None)
            if raw is not None and hasattr(raw, "watch_order_book"):
                tasks.append(asyncio.create_task(
                    self._watch_exchange_loop(ex, raw, self._symbols)
                ))
            else:
                tasks.append(asyncio.create_task(
                    self._poll_exchange_loop(ex, client, self._symbols)
                ))

        if not tasks:
            while self._running:
                await asyncio.sleep(1.0)
            return

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _watch_exchange_loop(
        self, exchange: str, ccxt_exchange: Any, symbols: List[str]
    ) -> None:
        self._ws_sources.add(exchange)

        async def _watch_one(symbol: str) -> None:
            if not self._running:
                return
            try:
                ccxt_symbol = self._normalize_symbol(symbol, exchange)
                book = await ccxt_exchange.watch_order_book(ccxt_symbol, limit=5)
                try:
                    from backend.services.market_data_hub import market_data_hub
                    market_data_hub.publish_l2_book(exchange, symbol, book, source="ws")
                except Exception:
                    from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache
                    if mid_cache.refresh_from_orderbook(exchange, symbol, book):
                        mid_cache.mark_ws_source(exchange, symbol)
                self._watch_updates += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._last_error = str(e)
                logger.debug("[WsFeed] watch %s/%s: %s", exchange, symbol, e)

        while self._running:
            try:
                await asyncio.gather(
                    *[_watch_one(sym) for sym in symbols],
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._last_error = str(e)
                await asyncio.sleep(self.POLL_INTERVAL_SEC)

    async def _poll_exchange_loop(
        self, exchange: str, client: Any, symbols: List[str]
    ) -> None:
        while self._running:
            async def _poll_one(symbol: str) -> None:
                try:
                    book = await client.get_orderbook(symbol, depth=5)
                    try:
                        from backend.services.market_data_hub import market_data_hub
                        market_data_hub.publish_l2_book(
                            exchange, symbol, book, source="rest_fallback"
                        )
                    except Exception:
                        from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache
                        if mid_cache.refresh_from_orderbook(exchange, symbol, book):
                            pass
                    self._poll_updates += 1
                except Exception as e:
                    self._last_error = str(e)
                    logger.debug("[WsFeed] poll %s/%s: %s", exchange, symbol, e)

            await asyncio.gather(
                *[_poll_one(sym) for sym in symbols],
                return_exceptions=True,
            )
            await asyncio.sleep(self.POLL_INTERVAL_SEC)

    @staticmethod
    def _normalize_symbol(symbol: str, exchange: str) -> str:
        """CCXT 永续 symbol 格式"""
        s = symbol.upper().replace("-", "").replace("_", "")
        if "/" in symbol:
            return symbol
        base = s.replace("USDT", "").replace("USD", "")
        if exchange in ("binance", "bybit", "okx", "gateio", "asterdex"):
            return f"{base}/USDT:USDT"
        return symbol

    def get_status(self) -> Dict[str, Any]:
        from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache

        status = mid_cache.get_status()
        status.update({
            "feed_running": self._running,
            "feed_started_at": self._started_at,
            "ws_sources": sorted(self._ws_sources),
            "poll_updates": self._poll_updates,
            "watch_updates": self._watch_updates,
            "symbols": self._symbols,
            "last_error": self._last_error,
        })
        return status


cross_exchange_ws_feed = CrossExchangeWsFeed.get_instance()


def start_ws_feed(symbols: Optional[List[str]] = None) -> bool:
    """启动 WS feed（幂等）"""
    enabled = True
    try:
        from backend.config.arb_config_loader import arb_config
        ws = getattr(arb_config, "ws_feed", None)
        if ws is not None:
            enabled = getattr(ws, "enabled", True)
    except Exception:
        pass
    if not enabled:
        return False
    return cross_exchange_ws_feed.start(symbols)


def stop_ws_feed() -> None:
    cross_exchange_ws_feed.stop()
