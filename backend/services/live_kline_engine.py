"""
LiveKlineEngine — 当前 K 线实时滚动引擎

行业标准做法：
- 历史 K 线固定、落库不可变；
- "正在形成中的当前 K 线" 随 tick（最新价/成交）在内存中实时滚动；
- 周期闭合时把上一根落库 + 广播，新的一根从交易所 forming bar 播种；
- 前端通过 WS 订阅 kline_update，秒级看到当前 K 线跳动。

本引擎与 kline_realtime_collector 协作：
- collector 每分钟/按周期从交易所拉到 forming bar 后调用 seed_bar() 播种/校准；
- ticker 轮询（asterdex 2s / 其他 WS 推流）通过 market_events 推送价格事件；
- 引擎按 (symbol, period) 更新当前 bar 的 high/low/close，并在秒级广播。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import asyncio
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PERIOD_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000,
}

DEFAULT_WATCH_PERIODS = ["1m", "3m", "5m", "15m", "30m", "1h", "4h"]
BROADCAST_INTERVAL_SEC = 1.0
EXCHANGE_ALIASES = {"aster": "asterdex"}
FORMING_REFRESH_INTERVAL_SEC = float(os.getenv("LIVE_KLINE_FORMING_REFRESH_S", "10"))
FORMING_REFRESH_PERIODS = ("1m", "5m", "15m")
FORMING_REFRESH_MAX_SYMBOLS = 40


def bucket_start(ts: float, period_sec: int) -> int:
    return int(ts) - (int(ts) % period_sec)


class LiveKlineEngine:
    """内存当前 K 线引擎（单例，独立线程广播）。"""

    def __init__(self):
        self._lock = threading.Lock()
        # key: (exchange, symbol, period) -> bar dict
        self._bars: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        # key -> last broadcast wall time
        self._last_broadcast: Dict[Tuple[str, str, str], float] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._started = False
        self._forming_thread: Optional[threading.Thread] = None
        self._forming_stop = threading.Event()
        self._last_forming_refresh = 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> bool:
        if self._started and self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._broadcast_loop,
            name="live-kline-engine",
            daemon=True,
        )
        self._thread.start()
        self._forming_stop.clear()
        self._forming_thread = threading.Thread(
            target=self._forming_loop,
            name="live-kline-forming-refresh",
            daemon=True,
        )
        self._forming_thread.start()
        try:
            from backend.services.market_events import subscribe_price_updates
            subscribe_price_updates(self.on_price_tick)
        except Exception as exc:
            logger.warning("[LiveKline] market_events 订阅失败: %s", exc)
        self._started = True
        logger.info(
            "[LiveKline] 当前K线引擎启动（广播节流 %.1fs，forming校准 %.1fs）",
            BROADCAST_INTERVAL_SEC, FORMING_REFRESH_INTERVAL_SEC,
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        self._forming_stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._forming_thread:
            self._forming_thread.join(timeout=5)
            self._forming_thread = None
        try:
            from backend.services.market_events import unsubscribe_price_updates
            unsubscribe_price_updates(self.on_price_tick)
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # 数据写入
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_exchange(exchange: str) -> str:
        ex = (exchange or "asterdex").strip().lower()
        return EXCHANGE_ALIASES.get(ex, ex)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return (symbol or "").upper().split("-")[0].split("/")[0]

    def seed_bar(
        self,
        exchange: str,
        symbol: str,
        period: str,
        bar: Dict[str, Any],
    ) -> None:
        """交易所 forming bar 播种/校准（由 kline_realtime_collector 调用）。"""
        ex = self._normalize_exchange(exchange)
        sym = self._normalize_symbol(symbol)
        period = (period or "1m").lower()
        if not bar:
            return
        ts = int(bar.get("timestamp") or 0)
        if ts <= 0:
            return
        try:
            clean = {
                "timestamp": ts,
                "open": float(bar.get("open") or 0),
                "high": float(bar.get("high") or 0),
                "low": float(bar.get("low") or 0),
                "close": float(bar.get("close") or 0),
                "volume": float(bar.get("volume") or 0),
                "updated_at": time.time(),
            }
        except (TypeError, ValueError):
            return
        if clean["close"] <= 0:
            return
        with self._lock:
            self._bars[(ex, sym, period)] = clean

    def on_price_tick(self, event: Dict[str, Any]) -> None:
        """market_events 价格事件回调：更新所有已播种周期的当前 K 线。"""
        symbol = self._normalize_symbol(str(event.get("symbol") or ""))
        if not symbol:
            return
        try:
            price = float(event.get("price") or 0)
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        ts = float(event.get("timestamp") or event.get("event_time") or time.time())
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = time.time()
        self.update_price(symbol, price, ts)

    def update_price(self, symbol: str, price: float, ts: Optional[float] = None) -> None:
        sym = self._normalize_symbol(symbol)
        event_ts = float(ts if ts is not None else time.time())
        changed: List[Tuple[str, str, str]] = []
        with self._lock:
            for (ex, s, period), bar in list(self._bars.items()):
                if s != sym:
                    continue
                period_sec = PERIOD_SECONDS.get(period)
                if not period_sec:
                    continue
                bar_ts = int(bar.get("timestamp") or 0)
                if bar_ts <= 0:
                    continue
                bucket_end = bar_ts + period_sec
                if event_ts >= bucket_end:
                    # 新周期：先建临时 bar，等交易所 forming bar 校准
                    new_ts = bucket_start(event_ts, period_sec)
                    bar.update({
                        "timestamp": new_ts,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": 0.0,
                        "updated_at": event_ts,
                    })
                else:
                    bar["high"] = max(float(bar.get("high") or price), price)
                    bar["low"] = min(float(bar.get("low") or price), price)
                    bar["close"] = price
                    bar["updated_at"] = event_ts
                changed.append((ex, s, period))
        for key in changed:
            self._maybe_broadcast(key, force=False)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_live_bar(
        self,
        exchange: str,
        symbol: str,
        period: str,
        lazy_seed: bool = True,
    ) -> Optional[Dict[str, Any]]:
        ex = self._normalize_exchange(exchange)
        sym = self._normalize_symbol(symbol)
        period = (period or "1m").lower()
        with self._lock:
            bar = self._bars.get((ex, sym, period))
            if bar:
                return dict(bar)
        if not lazy_seed:
            return None
        try:
            from backend.services.kline_data_service import kline_service
            rows = kline_service.get_klines_from_db(
                sym, period, 1, exchange=ex
            ) or []
            if rows:
                row = rows[-1]
                self.seed_bar(ex, sym, period, row)
                with self._lock:
                    bar = self._bars.get((ex, sym, period))
                    return dict(bar) if bar else None
        except Exception:
            pass
        return None

    def get_all_bars(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "exchange": ex,
                    "symbol": sym,
                    "period": period,
                    "bar": dict(bar),
                }
                for (ex, sym, period), bar in self._bars.items()
            ]

    def watched_symbols(self) -> set:
        """当前引擎在跟踪的币种（用于限制 ticker 事件发布范围）。"""
        with self._lock:
            return {sym for (_ex, sym, _period) in self._bars}

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            count = len(self._bars)
        return {
            "running": self.is_running,
            "bars": count,
            "broadcast_interval_sec": BROADCAST_INTERVAL_SEC,
            "forming_refresh_interval_sec": FORMING_REFRESH_INTERVAL_SEC,
        }

    # ------------------------------------------------------------------
    # 广播
    # ------------------------------------------------------------------

    def _broadcast_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    keys = list(self._bars.keys())
                for key in keys:
                    self._maybe_broadcast(key, force=False)
            except Exception as exc:
                logger.debug("[LiveKline] 广播循环异常: %s", exc)
            self._stop.wait(BROADCAST_INTERVAL_SEC)

    def _maybe_broadcast(self, key: Tuple[str, str, str], force: bool = False) -> None:
        ex, sym, period = key
        now = time.time()
        last = self._last_broadcast.get(key, 0.0)
        if not force and now - last < BROADCAST_INTERVAL_SEC:
            return
        with self._lock:
            bar = self._bars.get(key)
            if not bar:
                return
            snapshot = dict(bar)
        self._last_broadcast[key] = now
        try:
            from backend.services.klines_ws_publisher import broadcast_after_collection
            broadcast_after_collection(sym, period, snapshot)
        except Exception as exc:
            logger.debug("[LiveKline] 广播失败 %s/%s: %s", sym, period, exc)

    # ------------------------------------------------------------------
    # 形成中 K 线校准（成交量/真实 OHLC）
    # ------------------------------------------------------------------

    def _forming_loop(self) -> None:
        while not self._forming_stop.is_set():
            t0 = time.time()
            try:
                self._refresh_forming_bars()
            except Exception as exc:
                logger.debug("[LiveKline] forming 校准异常: %s", exc)
            elapsed = time.time() - t0
            wait = max(1.0, FORMING_REFRESH_INTERVAL_SEC - elapsed)
            self._forming_stop.wait(wait)

    def _refresh_forming_bars(self) -> None:
        """对引擎跟踪中的 symbol×period 拉取交易所 forming bar 并校准。"""
        with self._lock:
            keys = list(self._bars.keys())
        targets: Dict[str, List[str]] = {}
        for (ex, sym, period) in keys:
            if period in FORMING_REFRESH_PERIODS:
                targets.setdefault(ex, []).append(sym)
        for ex, symbols in targets.items():
            symbols = list(dict.fromkeys(symbols))[:FORMING_REFRESH_MAX_SYMBOLS]
            try:
                asyncio.run(self._fetch_and_seed(ex, symbols))
            except Exception as exc:
                logger.debug("[LiveKline] forming 校准 %s: %s", ex, exc)
        self._last_forming_refresh = time.time()

    async def _fetch_and_seed(self, exchange: str, symbols: List[str]) -> None:
        from backend.services.kline_collectors import ExchangeDataSourceFactory
        collector = ExchangeDataSourceFactory.get_collector(exchange)
        sem = asyncio.Semaphore(12)

        async def _one(sym: str, period: str) -> None:
            async with sem:
                try:
                    bar = await collector.fetch_current_kline(sym, period)
                except Exception:
                    return
                if not bar:
                    return
                self.seed_bar(exchange, sym, period, {
                    "timestamp": bar.timestamp,
                    "open": bar.open_price,
                    "high": bar.high_price,
                    "low": bar.low_price,
                    "close": bar.close_price,
                    "volume": bar.volume,
                })

        await asyncio.gather(
            *[
                _one(sym, period)
                for sym in symbols
                for period in FORMING_REFRESH_PERIODS
            ],
            return_exceptions=True,
        )


live_kline_engine = LiveKlineEngine()
