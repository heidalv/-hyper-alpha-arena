"""
MarketDataHub — 统一 WebSocket / 行情事件总线

Phase 4: 高吞吐全量 WS 数据底座
- 标准化 MarketEvent schema
- 订阅注册表 + 多播发布
- L2/Mid 热缓存（分片锁）
- 桥接 price_cache / mid_cache / event_bus
- CrossExchangeWsFeed 作为 Hub 消费者/生产者
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class MarketEventType(str, Enum):
    L2_BOOK = "l2_book"
    TRADE = "trade"
    TICK = "tick"
    FUNDING = "funding"
    ASSET_CTX = "asset_ctx"


@dataclass
class MarketEvent:
    event_type: MarketEventType
    exchange: str
    symbol: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts_local: float = field(default_factory=time.time)
    ts_exchange: float = 0.0
    source: str = "ws"  # ws | rest_fallback


@dataclass
class L2Snapshot:
    exchange: str
    symbol: str
    bids: List[List[float]]
    asks: List[List[float]]
    mid: float
    bid: float
    ask: float
    updated_at: float = field(default_factory=time.time)
    source: str = "ws"


Handler = Callable[[MarketEvent], None]


class SubscriptionRegistry:
    """{exchange, channel} → refcount，用于去重订阅（预留扩展）"""

    def __init__(self):
        self._refs: Dict[Tuple[str, str, str], int] = {}
        self._lock = threading.Lock()

    def subscribe(self, exchange: str, channel: str, symbol: str) -> bool:
        key = (exchange.lower(), channel, symbol.upper())
        with self._lock:
            count = self._refs.get(key, 0)
            self._refs[key] = count + 1
            return count == 0

    def unsubscribe(self, exchange: str, channel: str, symbol: str) -> bool:
        key = (exchange.lower(), channel, symbol.upper())
        with self._lock:
            count = self._refs.get(key, 0)
            if count <= 1:
                self._refs.pop(key, None)
                return True
            self._refs[key] = count - 1
            return False

    def list_active(self) -> List[Dict[str, str]]:
        with self._lock:
            return [
                {"exchange": k[0], "channel": k[1], "symbol": k[2]}
                for k in self._refs
            ]


class MarketDataHub:
    """单例行情 Hub"""

    RING_SIZE = 4096
    DEFAULT_STALE_SEC = 5.0

    _instance: Optional["MarketDataHub"] = None
    _inst_lock = threading.Lock()

    def __init__(self):
        self._running = False
        self._started_at = 0.0
        self._symbols: List[str] = []
        self._handlers: Dict[MarketEventType, List[Handler]] = defaultdict(list)
        self._wildcard_handlers: List[Handler] = []
        self._l2_store: Dict[Tuple[str, str], L2Snapshot] = {}
        self._shard_locks: Dict[int, threading.Lock] = {
            i: threading.Lock() for i in range(16)
        }
        self._ring: Deque[MarketEvent] = deque(maxlen=self.RING_SIZE)
        self._publish_count = 0
        self._drop_count = 0
        self._last_error = ""
        self._registry = SubscriptionRegistry()
        self._ws_sources: Set[str] = set()
        self._stale_ttl = self.DEFAULT_STALE_SEC
        self._rest_fallback_interval = 30.0
        self._disable_rest_market_stream = True
        self._primary_exchange = "asterdex"
        self._funding_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._asset_ctx_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # ticker 最新价存储: (exchange, symbol) -> (price, ts)，用于秒级取价
        self._ticker_store: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._watchdog_polls = 0

    @classmethod
    def get_instance(cls) -> "MarketDataHub":
        if cls._instance is None:
            with cls._inst_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _shard(self, exchange: str, symbol: str) -> int:
        return hash((exchange.lower(), symbol.upper())) % 16

    def _lock_for(self, exchange: str, symbol: str) -> threading.Lock:
        return self._shard_locks[self._shard(exchange, symbol)]

    def configure(self, symbols: Optional[List[str]] = None, stale_ttl_sec: float = 5.0) -> None:
        if symbols:
            self._symbols = list(dict.fromkeys(symbols))
        self._stale_ttl = stale_ttl_sec

    def start(self, symbols: Optional[List[str]] = None) -> bool:
        if self._running:
            return True
        self.configure(symbols)
        self._load_config()
        # 数据中心统一入口：主交易所跟随 active_exchange（asterdex），
        # 避免配置文件/默认值残留 hyperliquid 导致「交易 Aster、数据 HL」。
        try:
            from backend.services.exchange_config import get_active_exchange
            active = (get_active_exchange() or "asterdex").strip().lower()
            if active == "aster":
                active = "asterdex"
            self._primary_exchange = active
        except Exception:
            pass
        self._running = True
        self._started_at = time.time()
        self._wire_adapters()
        self._start_stale_watchdog()
        logger.info("[MarketDataHub] 启动 symbols=%s", self._symbols)
        return True

    def stop(self) -> None:
        self._running = False
        self._watchdog_stop.set()
        logger.info("[MarketDataHub] 已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def _load_config(self) -> None:
        try:
            from backend.config.arb_config_loader import arb_config

            hub = getattr(arb_config, "market_data_hub", None)
            if hub:
                if getattr(hub, "symbols", None):
                    self._symbols = list(hub.symbols)
                if getattr(hub, "stale_ttl_sec", None):
                    self._stale_ttl = float(hub.stale_ttl_sec)
                if getattr(hub, "rest_fallback_interval_sec", None):
                    self._rest_fallback_interval = float(hub.rest_fallback_interval_sec)
                if getattr(hub, "disable_rest_market_stream", None) is not None:
                    self._disable_rest_market_stream = bool(hub.disable_rest_market_stream)
                if getattr(hub, "primary_exchange", None):
                    self._primary_exchange = str(hub.primary_exchange).lower()
            else:
                ws = getattr(arb_config, "ws_feed", None)
                if ws and getattr(ws, "symbols", None):
                    self._symbols = list(ws.symbols)
                scanner = getattr(arb_config, "scanner", None)
                if scanner and getattr(scanner, "mid_cache_ttl_sec", None):
                    self._stale_ttl = float(scanner.mid_cache_ttl_sec)
        except Exception:
            pass

    def _wire_adapters(self) -> None:
        """Hub → mid_cache / price_cache 适配（幂等注册）"""
        if getattr(self, "_adapters_wired", False):
            return
        self._adapters_wired = True

        def _on_l2(ev: MarketEvent) -> None:
            book = ev.payload
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids or not asks:
                return
            try:
                from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache

                mid_cache.refresh_from_orderbook(ev.exchange, ev.symbol, book)
                if ev.source == "ws":
                    mid_cache.mark_ws_source(ev.exchange, ev.symbol)
            except Exception as e:
                logger.debug("[Hub] mid_cache adapter: %s", e)

            try:
                from backend.services.price_cache import price_cache

                mid = (float(bids[0][0]) + float(asks[0][0])) / 2
                price_cache.record(ev.symbol, "CRYPTO", mid)
            except Exception:
                pass

            try:
                from datetime import datetime, timezone
                from backend.services.market_events import publish_price_update

                mid = (float(bids[0][0]) + float(asks[0][0])) / 2
                publish_price_update({
                    "symbol": ev.symbol,
                    "market": "CRYPTO",
                    "price": mid,
                    "event_time": datetime.now(tz=timezone.utc),
                    "timestamp": ev.ts_local,
                    "source": "market_data_hub",
                    "exchange": ev.exchange,
                })
            except Exception:
                pass

        self.subscribe(MarketEventType.L2_BOOK, _on_l2)

        def _on_funding(ev: MarketEvent) -> None:
            key = (ev.exchange.lower(), ev.symbol.upper())
            with self._lock_for(ev.exchange, ev.symbol):
                self._funding_store[key] = {**ev.payload, "updated_at": ev.ts_local}

        def _on_asset_ctx(ev: MarketEvent) -> None:
            key = (ev.exchange.lower(), ev.symbol.upper())
            with self._lock_for(ev.exchange, ev.symbol):
                self._asset_ctx_store[key] = {**ev.payload, "updated_at": ev.ts_local}

        self.subscribe(MarketEventType.FUNDING, _on_funding)
        self.subscribe(MarketEventType.ASSET_CTX, _on_asset_ctx)

    def subscribe(
        self,
        event_type: Optional[MarketEventType],
        handler: Handler,
    ) -> None:
        if event_type is None:
            self._wildcard_handlers.append(handler)
        else:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def publish(self, event: MarketEvent) -> None:
        """发布事件 — 热路径尽量短"""
        self._publish_count += 1
        if event.source == "ws":
            self._ws_sources.add(event.exchange.lower())

        if event.event_type == MarketEventType.L2_BOOK:
            self._store_l2(event)

        try:
            self._ring.append(event)
        except Exception:
            self._drop_count += 1

        for h in self._handlers.get(event.event_type, []):
            try:
                h(event)
            except Exception as e:
                self._last_error = str(e)
                logger.debug("[Hub] handler error: %s", e)

        for h in self._wildcard_handlers:
            try:
                h(event)
            except Exception as e:
                logger.debug("[Hub] wildcard handler: %s", e)

        self._bridge_event_bus(event)

    def _store_l2(self, event: MarketEvent) -> None:
        book = event.payload
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        mid = (bid + ask) / 2
        snap = L2Snapshot(
            exchange=event.exchange.lower(),
            symbol=event.symbol,
            bids=bids,
            asks=asks,
            mid=mid,
            bid=bid,
            ask=ask,
            updated_at=event.ts_local,
            source=event.source,
        )
        key = (event.exchange.lower(), event.symbol.upper())
        lock = self._lock_for(event.exchange, event.symbol)
        with lock:
            self._l2_store[key] = snap

    def get_l2(self, exchange: str, symbol: str) -> Optional[L2Snapshot]:
        key = (exchange.lower(), symbol.upper())
        lock = self._lock_for(exchange, symbol)
        with lock:
            snap = self._l2_store.get(key)
            if snap is None:
                return None
            if time.time() - snap.updated_at > self._stale_ttl:
                return None
            return snap

    def get_mid(self, exchange: str, symbol: str) -> Optional[float]:
        snap = self.get_l2(exchange, symbol)
        return snap.mid if snap else None

    def get_price(
        self,
        symbol: str,
        exchange: Optional[str] = None,
    ) -> Optional[float]:
        """Hub mid → price_cache → None"""
        ex = (exchange or self._primary_exchange).lower()
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        ticker = self.get_ticker(ex, sym)
        if ticker is not None:
            return ticker
        mid = self.get_mid(ex, symbol)
        if mid is not None:
            return mid
        try:
            from backend.services.price_cache import get_cached_price
            return get_cached_price(symbol, "CRYPTO", "mainnet")
        except Exception:
            return None

    def publish_ticker_price(
        self,
        exchange: str,
        symbol: str,
        price: float,
        ts: Optional[float] = None,
    ) -> None:
        """写入 ticker 最新价（秒级取价优先来源）。"""
        ex = (exchange or self._primary_exchange).lower()
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        if price is None or price <= 0:
            return
        event_ts = ts if ts is not None else time.time()
        with self._lock_for(ex, sym):
            self._ticker_store[(ex, sym)] = (float(price), float(event_ts))

    def get_ticker(self, exchange: str, symbol: str) -> Optional[float]:
        """返回新鲜（<= stale_ttl）的 ticker 价格；过期视为缺失。"""
        entry = self.get_ticker_with_ts(exchange, symbol)
        return float(entry[0]) if entry else None

    def get_ticker_with_ts(self, exchange: str, symbol: str) -> Optional[Tuple[float, float]]:
        """返回新鲜（<= stale_ttl）的 ticker (price, ts)；过期视为缺失。

        [2026-08-07 价格权威口径] data_center.get_price_with_ts 的秒级来源之一，
        供调用方判断新鲜度。"""
        ex = (exchange or self._primary_exchange).lower()
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        with self._lock_for(ex, sym):
            entry = self._ticker_store.get((ex, sym))
            if not entry:
                return None
            price, ts = entry
            if time.time() - ts > self._stale_ttl:
                return None
            return (float(price), float(ts))

    def get_ticker_status(self) -> Dict[str, Any]:
        """ticker 覆盖统计（供 /hub/status 展示）。"""
        now = time.time()
        total = len(self._ticker_store)
        fresh = sum(
            1 for (_, ts) in self._ticker_store.values()
            if now - ts <= self._stale_ttl
        )
        return {"total": total, "fresh": fresh, "stale_ttl_sec": self._stale_ttl}

    def _normalize_ctx(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """HL WS activeAssetCtx 嵌套 ctx 字段"""
        if not raw:
            return {}
        inner = raw.get("ctx")
        if isinstance(inner, dict):
            return inner
        return raw

    def get_market_snapshot(
        self,
        symbol: str,
        exchange: Optional[str] = None,
    ) -> Dict[str, Any]:
        """供 unified_data_pool / API 使用的轻量快照（纯 Hub，不读 DB）"""
        ex = (exchange or self._primary_exchange).lower()
        sym = symbol.upper()
        key = (ex, sym)
        price = self.get_price(symbol, ex) or 0.0
        funding = 0.0
        oi = 0.0
        mark = 0.0
        volume_24h = 0.0
        price_24h_change_pct = 0.0

        with self._lock_for(ex, sym):
            f = self._funding_store.get(key, {})
            ctx = self._normalize_ctx(self._asset_ctx_store.get(key, {}))
        if f:
            funding = float(f.get("rate", f.get("funding", 0)) or 0)
        if ctx:
            funding = funding or float(ctx.get("funding", ctx.get("fundingRate", 0)) or 0)
            oi = float(ctx.get("openInterest", ctx.get("oi", 0)) or 0)
            mark = float(ctx.get("markPx", ctx.get("midPx", ctx.get("mark_price", 0))) or 0)
            volume_24h = float(ctx.get("dayNtlVlm", ctx.get("volume_24h", 0)) or 0)
            prev = float(ctx.get("prevDayPx", 0) or 0)
            if mark > 0 and prev > 0:
                price_24h_change_pct = (mark - prev) / prev * 100.0
        if not price and mark > 0:
            price = mark

        l2 = self.get_l2(ex, sym)
        return {
            "symbol": sym,
            "price": price,
            "funding_rate": funding,
            "open_interest": oi,
            "mark_price": mark,
            "volume_24h": volume_24h,
            "price_24h_change_pct": round(price_24h_change_pct, 4),
            "bid": l2.bid if l2 else 0.0,
            "ask": l2.ask if l2 else 0.0,
            "source": "market_data_hub",
            "exchange": ex,
            "stale": l2 is None,
        }

    def get_market_snapshots_batch(
        self,
        symbols: List[str],
        exchange: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        return {s.upper(): self.get_market_snapshot(s, exchange) for s in symbols}

    def get_prices_batch(
        self,
        symbols: List[str],
        exchange: Optional[str] = None,
    ) -> Dict[str, float]:
        return {
            s: p for s in symbols
            if (p := self.get_price(s, exchange)) is not None
        }

    def update_symbols(self, symbols: List[str]) -> None:
        self.configure(symbols=symbols)

    def should_disable_rest_market_stream(self) -> bool:
        return self._running and self._disable_rest_market_stream

    def _start_stale_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._stale_watchdog_loop,
            name="market-data-hub-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _stale_watchdog_loop(self) -> None:
        while self._running and not self._watchdog_stop.is_set():
            try:
                self._poll_stale_symbols()
            except Exception as e:
                self._last_error = str(e)
                logger.debug("[Hub] watchdog: %s", e)
            self._watchdog_stop.wait(self._rest_fallback_interval)

    def _poll_stale_symbols(self) -> None:
        """仅对 stale/missing 的 symbol 做 REST 兜底"""
        stale_syms = []
        for sym in self._symbols:
            if self.get_l2(self._primary_exchange, sym) is None:
                stale_syms.append(sym)
        if not stale_syms:
            return

        # 修复：asterdex 主所时用 asterdex ticker 内存价兜底，不再用 HL 价格冒充
        if self._primary_exchange == "asterdex":
            try:
                from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                for sym in stale_syms:
                    price = asterdex_ticker_poller.get_price(sym)
                    if not price or price <= 0:
                        continue
                    spread = float(price) * 0.0001
                    self.publish_l2_book(
                        "asterdex", sym,
                        {"bids": [[price - spread, 1.0]], "asks": [[price + spread, 1.0]]},
                        source="rest_fallback",
                    )
                self._watchdog_polls += 1
                return
            except Exception:
                pass

        try:
            # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止 HL REST 直连
            # 兜底盘口，改为从数据中心 DB 读价（asterdex 主所已由 ticker poller 兜底）。
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                from backend.services.data_center import data_center
                for sym in stale_syms:
                    try:
                        price = data_center.get_price(sym, self._primary_exchange)
                    except Exception:
                        price = None
                    if not price or price <= 0:
                        continue
                    spread = float(price) * 0.0001
                    self.publish_l2_book(
                        self._primary_exchange, sym,
                        {"bids": [[price - spread, 1.0]], "asks": [[price + spread, 1.0]]},
                        source="rest_fallback",
                    )
                self._watchdog_polls += 1
                return

            from backend.services.hyperliquid_market_data import get_default_hyperliquid_client

            client = get_default_hyperliquid_client()
            if not client.exchange:
                client._initialize_exchange()
            tickers = client.exchange.fetch_tickers(
                [client._format_symbol(s) for s in stale_syms]
            )
            for sym in stale_syms:
                formatted = client._format_symbol(sym)
                ticker = tickers.get(formatted)
                if not ticker:
                    continue
                last = ticker.get("last") or ticker.get("close")
                if not last:
                    continue
                price = float(last)
                spread = price * 0.0001
                self.publish_l2_book(
                    self._primary_exchange,
                    sym,
                    {
                        "bids": [[price - spread, 1.0]],
                        "asks": [[price + spread, 1.0]],
                    },
                    source="rest_fallback",
                )
            self._watchdog_polls += 1
        except Exception as e:
            logger.debug("[Hub] REST fallback: %s", e)

    def publish_l2_book(
        self,
        exchange: str,
        symbol: str,
        book: Dict[str, Any],
        source: str = "ws",
    ) -> None:
        self.publish(MarketEvent(
            event_type=MarketEventType.L2_BOOK,
            exchange=exchange,
            symbol=symbol,
            payload=book,
            source=source,
        ))

    def publish_trade(
        self,
        exchange: str,
        symbol: str,
        trade: Dict[str, Any],
        source: str = "ws",
    ) -> None:
        self.publish(MarketEvent(
            event_type=MarketEventType.TRADE,
            exchange=exchange,
            symbol=symbol,
            payload=trade,
            source=source,
        ))

    def publish_funding(
        self,
        exchange: str,
        symbol: str,
        funding: Dict[str, Any],
        source: str = "ws",
    ) -> None:
        self.publish(MarketEvent(
            event_type=MarketEventType.FUNDING,
            exchange=exchange,
            symbol=symbol,
            payload=funding,
            source=source,
        ))

    def publish_asset_ctx(
        self,
        exchange: str,
        symbol: str,
        ctx: Dict[str, Any],
        source: str = "ws",
    ) -> None:
        self.publish(MarketEvent(
            event_type=MarketEventType.ASSET_CTX,
            exchange=exchange,
            symbol=symbol,
            payload=ctx,
            source=source,
        ))

    def _bridge_event_bus(self, event: MarketEvent) -> None:
        """best-effort 桥接 asyncio EventBus（不阻塞 WS 热路径）"""
        try:
            from backend.services.event_bus import (
                EventType,
                EventPriority,
                MarketDataEvent,
                event_bus,
            )

            if event.event_type == MarketEventType.L2_BOOK:
                bids = event.payload.get("bids", [])
                asks = event.payload.get("asks", [])
                if not bids or not asks:
                    return
                mid = (float(bids[0][0]) + float(asks[0][0])) / 2
                ev = MarketDataEvent(
                    event_type=EventType.PRICE_UPDATE,
                    priority=EventPriority.LOW,
                    source="market_data_hub",
                    symbol=event.symbol,
                    data={"price": mid, "exchange": event.exchange, **event.payload},
                )
                event_bus.publish_sync(ev)
            elif event.event_type == MarketEventType.FUNDING:
                ev = MarketDataEvent(
                    event_type=EventType.FUNDING_RATE_UPDATE,
                    priority=EventPriority.LOW,
                    source="market_data_hub",
                    symbol=event.symbol,
                    data={"exchange": event.exchange, **event.payload},
                )
                event_bus.publish_sync(ev)
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        with self._inst_lock:
            stale = 0
            now = time.time()
            for snap in self._l2_store.values():
                if now - snap.updated_at > self._stale_ttl:
                    stale += 1
            return {
                "hub_running": self._running,
                "started_at": self._started_at,
                "symbols": self._symbols,
                "l2_entries": len(self._l2_store),
                "stale_entries": stale,
                "publish_count": self._publish_count,
                "drop_count": self._drop_count,
                "ws_sources": sorted(self._ws_sources),
                "subscriptions": self._registry.list_active(),
                "stale_ttl_sec": self._stale_ttl,
                "ring_size": len(self._ring),
                "last_error": self._last_error,
                "rest_fallback_interval_sec": self._rest_fallback_interval,
                "disable_rest_market_stream": self._disable_rest_market_stream,
                "watchdog_polls": self._watchdog_polls,
                "primary_exchange": self._primary_exchange,
                "ticker_status": self.get_ticker_status(),
            }


market_data_hub = MarketDataHub.get_instance()


def start_market_data_hub(symbols: Optional[List[str]] = None) -> bool:
    enabled = True
    try:
        from backend.config.arb_config_loader import arb_config

        hub_cfg = getattr(arb_config, "market_data_hub", None)
        if hub_cfg is not None:
            enabled = getattr(hub_cfg, "enabled", True)
    except Exception:
        pass
    if not enabled:
        return False
    market_data_hub.start(symbols)
    try:
        from backend.services.arbitrage.cross_exchange_ws_feed import start_ws_feed

        start_ws_feed(symbols)
    except Exception as e:
        logger.debug("[MarketDataHub] ws_feed start: %s", e)
    return True
