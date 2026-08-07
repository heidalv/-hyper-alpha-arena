"""
MarketPriceService — 统一取价与市场 symbol 同步

Phase 6: 取代 market_stream REST 轮询。
- 主路径：MarketDataHub WS + stale watchdog
- 取价：Hub → price_cache → REST 单次
- 降级：disable_rest_market_stream=false 时仍可启 LegacyRestPricePoller
"""

from __future__ import annotations

import logging
import threading
import time
import warnings
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Dict, Any

logger = logging.getLogger(__name__)

_legacy_poller: Optional["LegacyRestPricePoller"] = None


class LegacyRestPricePoller:
    """已废弃的 REST 定时轮询 — 仅 disable_rest_market_stream=false 时使用"""

    def __init__(
        self,
        symbols: Iterable[str],
        market: str = "CRYPTO",
        interval_seconds: float = 5.0,
    ) -> None:
        self.symbols = list(symbols)
        self.market = market
        self.interval_seconds = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="legacy-rest-price-poller", daemon=True
        )
        self._thread.start()
        logger.warning(
            "[LegacyRestPoller] 已启动 REST 轮询 %d symbols interval=%.1fs "
            "(建议设 market_data_hub.disable_rest_market_stream=true)",
            len(self.symbols),
            self.interval_seconds,
        )

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        logger.info("[LegacyRestPoller] 已停止")

    def update_symbols(self, symbols: Iterable[str]) -> None:
        self.symbols = list(symbols)

    def _run(self) -> None:
        from backend.services.hyperliquid_market_data import get_default_hyperliquid_client
        from backend.services.price_cache import record_price_update
        from backend.services.market_events import publish_price_update

        while not self._stop_event.is_set():
            t0 = time.time()
            try:
                # 按需取数：asterdex 主所用数据中心 ticker 内存价，禁止 HL 充数
                try:
                    from backend.services.exchange_config import get_active_exchange
                    _active = (get_active_exchange() or "asterdex").strip().lower()
                    if _active == "aster":
                        _active = "asterdex"
                except Exception:
                    _active = "asterdex"
                if _active == "asterdex":
                    from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                    event_time = datetime.now(tz=timezone.utc)
                    ts = event_time.timestamp()
                    for symbol in self.symbols:
                        price = asterdex_ticker_poller.get_price(symbol)
                        if not price or price <= 0:
                            continue
                        record_price_update(symbol, self.market, float(price), ts)
                        publish_price_update({
                            "symbol": symbol,
                            "market": self.market,
                            "price": float(price),
                            "event_time": event_time,
                            "timestamp": ts,
                            "source": "legacy_rest_poller",
                        })
                    elapsed = time.time() - t0
                    wait = max(0.0, self.interval_seconds - elapsed)
                    if wait > 0:
                        self._stop_event.wait(wait)
                    continue
                if _active not in ("hyperliquid",):
                    # 其他所：ccxt 按所批量取价
                    # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止
                    # 非 active 所 ccxt 直连批量取价，改为从数据中心 DB 批量读价
                    # 填充 Hub（保证唯一数据源的同时维持 Hub 快照新鲜度）。
                    from backend.services.market_data import _dc_only_enabled
                    if _dc_only_enabled():
                        self._fill_hub_from_db(_active)
                        elapsed = time.time() - t0
                        wait = max(0.0, self.interval_seconds - elapsed)
                        if wait > 0:
                            self._stop_event.wait(wait)
                        continue
                    from backend.services.market_aggregation.aggregate_collector_base import _create_ccxt_public
                    ccxt_ex = _create_ccxt_public(_active, timeout=10000)
                    if ccxt_ex is not None:
                        try:
                            tickers = ccxt_ex.fetch_tickers([
                                f"{s.upper()}/USDT:USDT" for s in self.symbols
                            ])
                            event_time = datetime.now(tz=timezone.utc)
                            ts = event_time.timestamp()
                            for symbol in self.symbols:
                                t = (tickers or {}).get(f"{symbol.upper()}/USDT:USDT")
                                if not t or not t.get("last"):
                                    continue
                                record_price_update(symbol, self.market, float(t["last"]), ts)
                                publish_price_update({
                                    "symbol": symbol, "market": self.market,
                                    "price": float(t["last"]), "event_time": event_time,
                                    "timestamp": ts, "source": "legacy_rest_poller",
                                })
                        finally:
                            try:
                                ccxt_ex.close()
                            except Exception:
                                pass
                        elapsed = time.time() - t0
                        wait = max(0.0, self.interval_seconds - elapsed)
                        if wait > 0:
                            self._stop_event.wait(wait)
                        continue
                # hyperliquid（或未识别）：保持原 HL 路径
                # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止 HL 直连
                # 批量取价（HL 行情由数据中心的 multi_venue_funding / market_flow 采集），
                # 改为从数据中心 DB 批量读价填充 Hub。
                from backend.services.market_data import _dc_only_enabled
                if _dc_only_enabled():
                    self._fill_hub_from_db("hyperliquid")
                    elapsed = time.time() - t0
                    wait = max(0.0, self.interval_seconds - elapsed)
                    if wait > 0:
                        self._stop_event.wait(wait)
                    continue
                client = get_default_hyperliquid_client()
                if not client.exchange:
                    client._initialize_exchange()
                tickers = client.exchange.fetch_tickers(
                    [client._format_symbol(s) for s in self.symbols]
                )
                event_time = datetime.now(tz=timezone.utc)
                ts = event_time.timestamp()
                for symbol in self.symbols:
                    formatted = client._format_symbol(symbol)
                    ticker = tickers.get(formatted)
                    if not ticker or not ticker.get("last"):
                        continue
                    price = float(ticker["last"])
                    record_price_update(symbol, self.market, price, ts)
                    publish_price_update({
                        "symbol": symbol,
                        "market": self.market,
                        "price": price,
                        "event_time": event_time,
                        "timestamp": ts,
                        "source": "legacy_rest_poller",
                    })
            except Exception as e:
                logger.debug("[LegacyRestPoller] batch fetch: %s", e)
            elapsed = time.time() - t0
            wait = max(0.0, self.interval_seconds - elapsed)
            if wait > 0:
                self._stop_event.wait(wait)

    def _fill_hub_from_db(self, exchange: str) -> None:
        """[2026-08-04 DC_ONLY] 数据中心唯一数据源：从数据中心 DB 批量读最新价
        填充 Hub 内存快照（替代被禁止的直连批量取价），维持 /prices 与
        unified_data_pool 的内存快照新鲜度。"""
        if not self.symbols:
            return
        try:
            from sqlalchemy import text as sa_text
            from backend.database.connection import MarketSessionLocal
            from backend.services.market_data_hub import market_data_hub
            from backend.services.price_cache import record_price_update
            from backend.services.market_events import publish_price_update

            ex = (exchange or "").strip().lower()
            if ex == "aster":
                ex = "asterdex"
            period = "1m"
            syms = [s.upper().split("-")[0].split("/")[0] for s in self.symbols]
            with MarketSessionLocal() as db:
                rows = db.execute(
                    sa_text(
                        """
                        SELECT DISTINCT ON (symbol) symbol, close_price
                        FROM crypto_klines
                        WHERE exchange = :ex AND symbol = ANY(:syms) AND period = :per
                        ORDER BY symbol, timestamp DESC
                        """
                    ),
                    {"ex": ex, "syms": syms, "per": period},
                ).fetchall()
            if not rows:
                return
            event_time = datetime.now(tz=timezone.utc)
            ts = event_time.timestamp()
            for r in rows:
                sym = str(r[0]).upper()
                price = float(r[1])
                if price <= 0:
                    continue
                market_data_hub.publish_ticker_price(ex, sym, price, ts)
                record_price_update(sym, self.market, price, ts)
                publish_price_update({
                    "symbol": sym, "market": self.market,
                    "price": price, "event_time": event_time,
                    "timestamp": ts, "source": "dc_db_poller",
                })
        except Exception as e:
            logger.debug("[LegacyRestPoller] fill hub from db: %s", e)


def get_price(symbol: str, exchange: Optional[str] = None) -> Optional[float]:
    """Hub mid → price_cache → REST 单次"""
    try:
        from backend.services.market_data_hub import market_data_hub
        p = market_data_hub.get_price(symbol, exchange)
        if p is not None:
            return p
    except Exception:
        pass
    try:
        from backend.services.price_cache import get_cached_price
        p = get_cached_price(symbol, "CRYPTO", "mainnet")
        if p:
            return float(p)
    except Exception:
        pass
    # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连兜底，
    # 统一从数据中心 DB 读取（数据由采集器落库，保证唯一数据源）。
    try:
        from backend.services.market_data import _dc_only_enabled
        if _dc_only_enabled():
            from backend.services.data_center import data_center
            p = data_center.get_price(symbol, exchange or None)
            if p and p > 0:
                return float(p)
            return None
    except Exception:
        pass
    try:
        # [2026-08-07 修复] 移除 ccxt REST 同步兜底：每次请求新建 ccxt 实例 +
        # 8s 无严格超时同步调用是 backend 线程池被占满的阻塞源之一。
        # 仅保留内存源（asterdex ticker poller / hyperliquid 缓存客户端）；
        # 其余所一律由 DC 采集器落库后走 data_center（DC_ONLY 唯一数据源）。
        ex = (exchange or "asterdex").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        if ex == "asterdex":
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
            return asterdex_ticker_poller.get_price(symbol)
        if ex == "hyperliquid":
            from backend.services.hyperliquid_market_data import get_default_hyperliquid_client
            return get_default_hyperliquid_client().get_last_price(symbol)
    except Exception:
        pass
    return None


def get_prices_batch(
    symbols: List[str],
    exchange: Optional[str] = None,
) -> dict[str, float]:
    return {s: p for s in symbols if (p := get_price(s, exchange)) is not None}


def sync_market_symbols(
    symbols: List[str],
    interval_seconds: float = 5.0,
) -> None:
    """同步 Hub symbol 列表；仅在显式允许时启 Legacy REST 轮询"""
    global _legacy_poller

    try:
        from backend.services.market_data_hub import market_data_hub
        market_data_hub.update_symbols(symbols)
        if market_data_hub.should_disable_rest_market_stream():
            logger.debug(
                "[MarketPriceService] symbols synced to Hub (%d), REST 轮询跳过",
                len(symbols),
            )
            return
    except Exception as e:
        logger.debug("[MarketPriceService] hub sync: %s", e)

    if _legacy_poller and _legacy_poller._thread and _legacy_poller._thread.is_alive():
        _legacy_poller.update_symbols(symbols)
        return

    _legacy_poller = LegacyRestPricePoller(symbols, interval_seconds=interval_seconds)
    _legacy_poller.start()


def stop_market_price_services() -> None:
    """停止 Legacy REST 轮询 + MarketDataHub"""
    global _legacy_poller
    if _legacy_poller:
        _legacy_poller.stop()
        _legacy_poller = None
    try:
        from backend.services.market_data_hub import market_data_hub
        market_data_hub.stop()
    except Exception:
        pass
    try:
        from backend.services.arbitrage.cross_exchange_ws_feed import stop_ws_feed
        stop_ws_feed()
    except Exception:
        pass


def is_legacy_rest_poller_running() -> bool:
    return (
        _legacy_poller is not None
        and _legacy_poller._thread is not None
        and _legacy_poller._thread.is_alive()
    )


def get_market_snapshot(symbol: str, exchange: Optional[str] = None) -> Dict[str, Any]:
    """单 symbol Hub 快照"""
    try:
        from backend.services.market_data_hub import market_data_hub
        return market_data_hub.get_market_snapshot(symbol, exchange)
    except Exception:
        return {"symbol": symbol.upper(), "price": get_price(symbol, exchange) or 0.0}


def get_market_snapshots(
    symbols: List[str],
    exchange: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """批量 Hub 快照 — REST API / unified_data_pool 共用"""
    try:
        from backend.services.market_data_hub import market_data_hub
        return market_data_hub.get_market_snapshots_batch(symbols, exchange)
    except Exception:
        return {
            s.upper(): {"symbol": s.upper(), "price": get_price(s, exchange) or 0.0}
            for s in symbols
        }
