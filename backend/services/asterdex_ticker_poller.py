"""
Asterdex 全市场 Ticker 高频轮询器

实测确认：asterdex 没有 WebSocket（wss://fapi.asterdex.com/ws 与 /stream 均 404），
只能走 REST。REST /fapi/v1/ticker/price 全市场约 532 对，实测 ~600ms，
因此 2 秒轮询一次全市场价格是可行的。

职责：
1. 每 2 秒拉取全市场最新价，写入 price_cache（TTL 60s）供全站取价；
2. 通过 market_events 发布价格事件（供 LiveKlineEngine / 策略订阅）；
3. 同步到 MarketDataHub 的 ticker 存储，保证 get_price 优先返回新鲜 ticker，
   避免被陈旧的盘口中间价挡住。
4. ensure_snapshot()：API 在 DATA_CENTER_MODE=standalone 时写端不在本进程，
   读总览前可按需补齐本进程内存快照。

用法：
    from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
    asterdex_ticker_poller.start()
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.services.symbol_normalizer import normalize_symbol

logger = logging.getLogger(__name__)

ASTERDEX_TICKER_URL = "https://fapi.asterdex.com/fapi/v1/ticker/price"
ASTERDEX_TICKER_24H_URL = "https://fapi.asterdex.com/fapi/v1/ticker/24hr"
DEFAULT_INTERVAL_SEC = float(os.getenv("ASTERDEX_TICKER_INTERVAL_S", "2"))
STATS_INTERVAL_SEC = float(os.getenv("ASTERDEX_STATS_INTERVAL_S", "5"))


def _default_proxy() -> Optional[str]:
    for key in (
        "MARKET_DATA_HTTP_PROXY",
        "BINANCE_HTTP_PROXY",
        "BINANCE_HTTPS_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
    ):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return None


def _make_opener() -> urllib.request.OpenerDirector:
    proxy = _default_proxy()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        if proxy
        else urllib.request.ProxyHandler({})
    )


# [perf 2026-08-18] opener 复用：build_opener 每次都会加载 Windows 证书库
# （ssl.load_default_certs，持 GIL ~百毫秒级）；ensure_snapshot 在 API 请求
# 路径上被高频调用，改为模块级单例。
_shared_opener: Optional[urllib.request.OpenerDirector] = None
_opener_lock = threading.Lock()


def _get_opener() -> urllib.request.OpenerDirector:
    global _shared_opener
    if _shared_opener is None:
        with _opener_lock:
            if _shared_opener is None:
                _shared_opener = _make_opener()
    return _shared_opener


def _wait_rate(bucket: str = "live") -> bool:
    """走 Asterdex 全局限速器；冷却中返回 False（调用方跳过本轮请求）。"""
    try:
        from backend.services.kline_collectors import _AsterdexRateLimiter

        _AsterdexRateLimiter.wait(bucket)
        return True
    except Exception as exc:
        # [2026-08-06] WARNING→DEBUG：冷却跳过是预期流程（每轮询周期一次），
        # 降级避免每 30-60s 刷一条 WARNING 淹没日志。
        logger.debug("[AsterdexTicker] 全局限速冷却跳过轮询: %s", exc)
        return False


def _note_banned_if_rate_limited(exc: Exception) -> None:
    """429/418 时触发全局冷却，让 K 线/回填等组件同步停手。"""
    msg = str(exc).lower()
    if any(k in msg for k in ("429", "418", "too many requests", "way too many", "banned")):
        try:
            from backend.services.kline_collectors import _AsterdexRateLimiter

            _AsterdexRateLimiter.note_banned()
        except Exception:
            pass


class AsterdexTickerPoller:
    """asterdex 全市场 ticker 轮询（线程 + urllib，走本地代理）。"""

    def __init__(self, interval_seconds: float = DEFAULT_INTERVAL_SEC):
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        # base symbol -> (price, ts)
        self._prices: Dict[str, Tuple[float, float]] = {}
        # base symbol -> 24h 统计（价格/涨跌/高低/成交量）
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._last_error_ts = 0.0
        self._last_stats_ts = 0.0
        self._polls = 0
        self._last_poll_at = 0.0
        # [2026-08-15 D5] 秒级 ticker 落库缓冲（仅数据中心进程开启）
        self._snap_buffer: List[Tuple[str, float, int]] = []
        self._snap_flusher: Optional[threading.Thread] = None
        self._snap_symbols: set = set()
        self._snap_dropped = 0
        self._snap_written = 0

    # ── [2026-08-15 D5] ticker 快照持久化 ──────────────────────
    def _snapshot_enabled(self) -> bool:
        return os.getenv("TICKER_SNAPSHOT_PERSIST", "false").strip().lower() in (
            "1", "true", "yes", "on",
        )

    def _snapshot_symbols(self) -> set:
        """落库符号集：research 热币（≤150）+ BTC/ETH/SOL，控制体积。"""
        syms = {"BTC", "ETH", "SOL"}
        try:
            from backend.services.kline_realtime_collector import get_research_priority_symbols
            for s in get_research_priority_symbols(limit=150):
                syms.add(s)
        except Exception:
            pass
        return syms

    def _buffer_snapshot(self, prices: Dict[str, Tuple[float, float]]) -> None:
        """把本轮 ticker 价格追加到落库缓冲（每 10s 由 flusher 批量写入）。"""
        if not self._snap_flusher or not self._snap_flusher.is_alive():
            return
        now_ms = int(time.time() * 1000)
        with self._lock:
            if not self._snap_symbols:
                self._snap_symbols = self._snapshot_symbols()
            syms = self._snap_symbols
            cap = 20000
            for base, (price, _ts) in prices.items():
                if base not in syms:
                    continue
                if len(self._snap_buffer) >= cap:
                    self._snap_buffer.pop(0)
                    self._snap_dropped += 1
                self._snap_buffer.append((base, float(price), now_ms))

    def _flush_snapshots(self) -> None:
        """落库线程：每 10s 批量写 ticker_snapshots（失败仅 debug 计数，不阻塞采集）。"""
        while not self._stop.is_set():
            self._stop.wait(10.0)
            with self._lock:
                if not self._snap_buffer:
                    continue
                batch = self._snap_buffer
                self._snap_buffer = []
            if not batch:
                continue
            try:
                from sqlalchemy import text as _sa_text

                from backend.database.connection import MarketSessionLocal
                rows = [
                    {"exchange": "asterdex", "symbol": s, "price": p, "ts_ms": ts}
                    for s, p, ts in batch
                ]
                with MarketSessionLocal() as db:
                    db.execute(
                        _sa_text(
                            "INSERT INTO ticker_snapshots (exchange, symbol, price, ts_ms) "
                            "VALUES (:exchange, :symbol, :price, :ts_ms)"
                        ),
                        rows,
                    )
                    db.commit()
                self._snap_written += len(rows)
            except Exception as exc:
                # 建表未完成（老 DC 进程未重启）等场景：计数并继续
                logger.debug("[AsterdexTicker] ticker 快照落库失败: %s", exc)

    def start_snapshot_flusher(self) -> None:
        """在数据中心进程开启 ticker 落库线程（幂等）。"""
        if self._snap_flusher and self._snap_flusher.is_alive():
            return
        if not self._snapshot_enabled():
            return
        self._snap_flusher = threading.Thread(
            target=self._flush_snapshots,
            name="asterdex-ticker-snapshot-flusher",
            daemon=True,
        )
        self._snap_flusher.start()
        logger.info("[AsterdexTicker] ticker 快照落库已开启（10s 批量，14 天保留）")

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="asterdex-ticker-poller",
            daemon=True,
        )
        self._thread.start()
        self.start_snapshot_flusher()
        logger.info(
            "[AsterdexTicker] 启动：全市场 ticker 每 %.1fs 轮询", self.interval_seconds
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_price(self, symbol: str) -> Optional[float]:
        """返回最近一次 ticker 价格（不做新鲜度校验，由调用方判断）。"""
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        with self._lock:
            entry = self._prices.get(sym)
            return float(entry[0]) if entry else None

    def get_price_with_ts(self, symbol: str) -> Optional[Tuple[float, float]]:
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        with self._lock:
            entry = self._prices.get(sym)
            return entry if entry else None

    def get_all_prices(self) -> Dict[str, float]:
        with self._lock:
            return {s: p for s, (p, _) in self._prices.items()}

    def get_status(self) -> Dict:
        with self._lock:
            count = len(self._prices)
        return {
            "running": self.is_running,
            "interval_sec": self.interval_seconds,
            "polls": self._polls,
            "last_poll_at": self._last_poll_at,
            "symbols": count,
            "stats_symbols": len(self._stats),
        }

    def ensure_snapshot(
        self,
        max_age_sec: float = 25.0,
        include_stats: bool = True,
        fan_out: bool = True,
    ) -> int:
        """按需拉一次全市场快照（供 API 在 standalone 模式下读总览）。

        写端在数据中心进程时，主 API 的 poller 未 start，内存为空会导致
        /market/overview/all 返回 0 行。本方法不依赖 start()。
        """
        now = time.time()
        with self._lock:
            n = len(self._prices)
            age = (now - self._last_poll_at) if self._last_poll_at else 1e9
            stats_n = len(self._stats)
            stats_age = (now - self._last_stats_ts) if self._last_stats_ts else 1e9
        need_price = n == 0 or age > max_age_sec
        need_stats = include_stats and (
            stats_n == 0 or stats_age > max(max_age_sec, STATS_INTERVAL_SEC)
        )
        if not need_price and not need_stats:
            return n

        if not self._refresh_lock.acquire(blocking=False):
            time.sleep(0.4)
            with self._lock:
                return len(self._prices)
        try:
            now = time.time()
            with self._lock:
                n = len(self._prices)
                age = (now - self._last_poll_at) if self._last_poll_at else 1e9
                stats_n = len(self._stats)
                stats_age = (now - self._last_stats_ts) if self._last_stats_ts else 1e9
            need_price = n == 0 or age > max_age_sec
            need_stats = include_stats and (
                stats_n == 0 or stats_age > max(max_age_sec, STATS_INTERVAL_SEC)
            )
            if not need_price and not need_stats:
                return n

            opener = _get_opener()
            if need_price:
                if _wait_rate("live"):
                    self._fetch_once(opener, do_fan_out=fan_out)
            if need_stats:
                if _wait_rate("live"):
                    self._fetch_stats_once(opener)
                    self._last_stats_ts = time.time()
            with self._lock:
                return len(self._prices)
        except Exception as exc:
            now = time.time()
            if now - self._last_error_ts > 10:
                logger.warning("[AsterdexTicker] ensure_snapshot 失败: %s", exc)
                self._last_error_ts = now
            with self._lock:
                return len(self._prices)
        finally:
            self._refresh_lock.release()

    def _run(self) -> None:
        opener = _make_opener()
        while not self._stop.is_set():
            t0 = time.time()
            ok = True
            try:
                if _wait_rate("live"):
                    self._fetch_once(opener)
                if time.time() - self._last_stats_ts >= STATS_INTERVAL_SEC:
                    if _wait_rate("live"):
                        self._fetch_stats_once(opener)
                        self._last_stats_ts = time.time()
            except Exception as exc:
                ok = False
                now = time.time()
                if now - self._last_error_ts > 10:
                    # [2026-08-06] WARNING→DEBUG：SSL EOF/网络抖动高频出现，
                    # 保留 10s 节流 + 数据质量计数（record_source_call），
                    # 降级日志避免刷屏（实测每 30-40s 一条）。
                    logger.debug("[AsterdexTicker] 轮询失败: %s", exc)
                    self._last_error_ts = now
            # [v6 2.3] 行情链路健康记录（前端行情链路卡数据源）
            try:
                from backend.services.data_quality_monitor import get_data_quality_monitor
                get_data_quality_monitor().record_source_call(
                    "ticker_asterdex", success=ok,
                    latency_ms=round((time.time() - t0) * 1000, 1),
                    error="" if ok else f"poll failed ({exc.__class__.__name__})",
                )
            except Exception:
                pass
            elapsed = time.time() - t0
            wait = max(0.0, self.interval_seconds - elapsed)
            if wait > 0:
                self._stop.wait(wait)

    def _fetch_stats_once(self, opener: urllib.request.OpenerDirector) -> None:
        """全市场 24h 统计（~700ms / 191KB），供数据总览表格使用。"""
        try:
            with opener.open(ASTERDEX_TICKER_24H_URL, timeout=10) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            _note_banned_if_rate_limited(exc)
            raise
        if not isinstance(raw, list):
            return
        stats: Dict[str, Dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            raw_symbol = str(item.get("symbol") or "")
            if not raw_symbol.endswith("USDT"):
                continue
            base = normalize_symbol(raw_symbol)
            if not base:
                continue

            def _f(key: str, default: float = 0.0) -> float:
                try:
                    return float(item.get(key) or default)
                except (TypeError, ValueError):
                    return default

            last = _f("lastPrice")
            if last <= 0:
                continue
            stats[base] = {
                "price": last,
                "change_pct": _f("priceChangePercent"),
                "high_24h": _f("highPrice"),
                "low_24h": _f("lowPrice"),
                "volume_24h": _f("volume"),
                "quote_volume_24h": _f("quoteVolume"),
                "weighted_avg": _f("weightedAvgPrice"),
                "trades_24h": int(_f("count")),
                "ts": time.time(),
            }
        if stats:
            with self._lock:
                self._stats = stats

    def get_stats(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = (symbol or "").upper().split("-")[0].split("/")[0]
        with self._lock:
            entry = self._stats.get(sym)
            return dict(entry) if entry else None

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {s: dict(v) for s, v in self._stats.items()}

    def _fetch_once(
        self,
        opener: urllib.request.OpenerDirector,
        do_fan_out: bool = True,
    ) -> None:
        try:
            with opener.open(ASTERDEX_TICKER_URL, timeout=8) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            _note_banned_if_rate_limited(exc)
            raise
        now_ts = time.time()
        if not isinstance(raw, list):
            return

        fresh: Dict[str, Tuple[float, float]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            raw_symbol = str(item.get("symbol") or "")
            if not raw_symbol.endswith("USDT"):
                continue
            base = normalize_symbol(raw_symbol)
            if not base:
                continue
            try:
                price = float(item.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            fresh[base] = (price, now_ts)

        if not fresh:
            return

        with self._lock:
            self._prices = fresh
            self._polls += 1
            self._last_poll_at = now_ts

        self._buffer_snapshot(fresh)

        if do_fan_out:
            self._fan_out(fresh, now_ts)

    def _publish_targets(self) -> Dict[str, Tuple[float, float]]:
        """事件发布范围：只发实时引擎跟踪中的币，避免 527 币 × 2s 轰炸策略层。"""
        try:
            from backend.services.live_kline_engine import live_kline_engine
            watched = live_kline_engine.watched_symbols() or {"BTC", "ETH", "SOL"}
        except Exception:
            watched = {"BTC", "ETH", "SOL"}
        with self._lock:
            return {
                sym: entry for sym, entry in self._prices.items()
                if sym in watched
            }

    def _fan_out(self, prices: Dict[str, Tuple[float, float]], ts: float) -> None:
        """写入 price_cache + MarketDataHub ticker 存储 + market_events。"""
        try:
            from backend.services.price_cache import record_price_update

            for base, (price, _) in prices.items():
                try:
                    record_price_update(base, "CRYPTO", price, ts)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[AsterdexTicker] price_cache 写入失败: %s", exc)

        try:
            from backend.services.market_data_hub import market_data_hub

            for base, (price, _) in prices.items():
                try:
                    market_data_hub.publish_ticker_price("asterdex", base, price, ts)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[AsterdexTicker] MarketDataHub 写入失败: %s", exc)

        targets = self._publish_targets()
        if targets:
            try:
                from backend.services.market_events import publish_price_update
                event_time = datetime.now(tz=timezone.utc)
                for base, (price, _) in targets.items():
                    try:
                        publish_price_update({
                            "symbol": base,
                            "market": "CRYPTO",
                            "price": price,
                            "event_time": event_time,
                            "timestamp": ts,
                            "source": "asterdex_ticker_poller",
                            "exchange": "asterdex",
                        })
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("[AsterdexTicker] market_events 发布失败: %s", exc)


asterdex_ticker_poller = AsterdexTickerPoller()
