"""
K线数据采集器 - 交易所分流架构
"""

import asyncio
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from .kline_collector_executor import run_kline_io

# 同步 ccxt 非线程安全：按线程缓存实例，避免每次 fetch 都 load_markets 拖死 P1。
_tls = threading.local()


class ExchangeRateLimitError(Exception):
    """交易所限流/封禁（429/418/-1003）。由采集器上抛，驱动上层冷却降速。"""


def _is_rate_limited_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        k in msg
        for k in ("429", "418", "-1003", "banned", "too many requests", "way too many")
    )


class _AsterdexRateLimiter:
    """[2026-08-04 修复] Asterdex 进程级滑动窗口限速（双桶 + 全局封禁）。

    - live 桶：P0/P1 短线实时采集（fetch_current_kline），默认 1200 req/min。
    - backfill 桶：P2 深历史回填（fetch_historical_klines），默认 150 req/min，
      慢速渐进，不与 live 抢配额。

    任一链路命中 429/418 时：
    1. 置全局 `_banned_until`（ASTERDEX_RATE_BACKOFF_SEC，默认 180s）；
    2. 冷却期内所有 bucket 的 wait() 直接抛 ExchangeRateLimitError（fail fast，
       不发真实网络请求），各采集器据此进入自己的冷却/快速跳过；
    3. 从而实现「一个组件触发限流 → 全链路同步停手」，
       避免某个组件持续撞 429 窗口导致自激循环。
    """
    _lock = threading.Lock()
    _req_ts: Dict[str, List[float]] = {"live": [], "backfill": []}
    _max_per_min = {
        "live": max(60, int(os.getenv("ASTERDEX_MAX_REQ_PER_MIN", "1200"))),
        "backfill": max(30, int(os.getenv("ASTERDEX_BACKFILL_MAX_REQ_PER_MIN", "150"))),
    }
    _banned_until: float = 0.0
    _ban_backoff = max(60.0, float(os.getenv("ASTERDEX_RATE_BACKOFF_SEC", "90")))

    @classmethod
    def note_banned(cls) -> None:
        """任一 Asterdex 请求命中 429/418 时调用：全局冷却，所有桶停止发包。"""
        with cls._lock:
            cls._banned_until = time.time() + cls._ban_backoff

    @classmethod
    def banned_remaining(cls) -> float:
        """剩余全局冷却秒数（<=0 表示不在冷却）。供 market_flow 等独立组件检查。"""
        with cls._lock:
            return max(0.0, cls._banned_until - time.time())

    @classmethod
    def wait(cls, bucket: str = "live") -> None:
        bucket = bucket if bucket in cls._max_per_min else "live"
        with cls._lock:
            if time.time() < cls._banned_until:
                raise ExchangeRateLimitError(
                    "Asterdex 全局限流冷却中（%.0fs 后恢复），本轮不发请求"
                    % (cls._banned_until - time.time())
                )
            now = time.time()
            ts_list = cls._req_ts.setdefault(bucket, [])
            cls._req_ts[bucket] = [t for t in ts_list if now - t < 60.0]
            if len(cls._req_ts[bucket]) >= cls._max_per_min[bucket]:
                wait_s = 60.0 - (now - cls._req_ts[bucket][0]) + 0.05
            else:
                wait_s = 0.0
        if wait_s > 0:
            time.sleep(wait_s)
        with cls._lock:
            cls._req_ts.setdefault(bucket, []).append(time.time())


class _ColdExchangeRateLimiter:
    """[2026-08-04 修复] 冷所（binance/okx/bybit/gateio）进程级限速。

    Asterdex 是全站唯一主数据源，已有 _AsterdexRateLimiter 双桶管控；
    但冷所（备选源）此前完全裸奔 —— P1 并发 4 + 冷所深回填叠加时
    触发冷所自身 429（实测 okx 大面积 "Too Many Requests"，并连累
    P0/P1 全链冷却）。

    本限速器：
    - 每个冷所独立滑动窗口（默认 COLD_EXCHANGE_MAX_REQ_PER_MIN=180）；
    - 命中 429 只冷却该所（COLD_EXCHANGE_RATE_BACKOFF_SEC=60），
      不全局停手（冷所彼此独立，且 Asterdex 是主数据源不应被连累）。
    """

    _lock = threading.Lock()
    _req_ts: Dict[str, List[float]] = {}
    _banned_until: Dict[str, float] = {}

    # 每个冷所两次请求之间的最小间隔（秒）。默认取 KLINE_REQUEST_INTERVAL_SEC，
    # 防止 P1 并发任务在同一瞬间同时放行造成突发打满交易所每秒限额。
    _min_interval = max(0.2, float(os.getenv("KLINE_REQUEST_INTERVAL_SEC", "0.3")))

    def _max_per_min(exchange: str) -> int:
        try:
            v = int(os.getenv(
                f"COLD_EXCHANGE_MAX_REQ_PER_MIN_{exchange.upper()}",
                os.getenv("COLD_EXCHANGE_MAX_REQ_PER_MIN", "180"),
            ))
            return max(30, v)
        except (TypeError, ValueError):
            return 180

    _ban_backoff = max(30.0, float(os.getenv("COLD_EXCHANGE_RATE_BACKOFF_SEC", "60")))

    @classmethod
    def note_banned(cls, exchange: str) -> None:
        """该冷所命中 429/418：仅冷却本所，不影响其它所。"""
        with cls._lock:
            cls._banned_until[exchange] = time.time() + cls._ban_backoff

    @classmethod
    def banned_remaining(cls, exchange: str) -> float:
        with cls._lock:
            return max(0.0, cls._banned_until.get(exchange, 0.0) - time.time())

    @classmethod
    def wait(cls, exchange: str) -> None:
        """请求前调用：冷却中抛 ExchangeRateLimitError（fail fast），否则滑动窗口限速。"""
        with cls._lock:
            if time.time() < cls._banned_until.get(exchange, 0.0):
                raise ExchangeRateLimitError(
                    "%s 限流冷却中（%.0fs 后恢复），本轮不发请求"
                    % (exchange, cls._banned_until.get(exchange, 0.0) - time.time())
                )
            now = time.time()
            ts_list = cls._req_ts.setdefault(exchange, [])
            cls._req_ts[exchange] = [t for t in ts_list if now - t < 60.0]
            if len(cls._req_ts[exchange]) >= cls._max_per_min(exchange):
                wait_s = 60.0 - (now - cls._req_ts[exchange][0]) + 0.05
            else:
                wait_s = 0.0
            if wait_s > 0:
                time.sleep(wait_s)
                now = time.time()
            # 在锁内做最小间隔排队：并发任务不会同一瞬间全部放行。
            last_ts = cls._req_ts[exchange][-1] if cls._req_ts[exchange] else 0.0
            gap = cls._min_interval - (now - last_ts)
            if gap > 0:
                time.sleep(gap)
            cls._req_ts.setdefault(exchange, []).append(time.time())


_COLD_EXCHANGES = ("binance", "okx", "bybit", "gateio")


@dataclass
class KlineData:
    """标准化的K线数据结构"""
    exchange: str
    symbol: str
    timestamp: int  # Unix timestamp in seconds
    period: str     # "1m", "5m", "1h", etc.
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


def _proxy_kwargs() -> dict:
    proxy = (
        os.getenv("BINANCE_HTTPS_PROXY")
        or os.getenv("MARKET_DATA_HTTP_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("HTTP_PROXY")
    )
    kwargs: dict = {"timeout": 20000, "enableRateLimit": True}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return kwargs


def _ccxt_symbol(exchange_id: str, symbol: str) -> str:
    """统一 base → 永续合约 ccxt 符号。OKX 必须用 BTC/USDT:USDT。"""
    base = (symbol or "").upper().split("-")[0].split("/")[0]
    if exchange_id == "okx":
        return f"{base}/USDT:USDT"
    if exchange_id in ("binance", "asterdex", "bybit", "gateio"):
        return f"{base}/USDT:USDT"
    return f"{base}/USDT"


def _create_sync_ccxt(exchange_id: str):
    """新建同步 ccxt 实例（禁止复用 async adapter，避免跨 event loop Lock）。"""
    import ccxt as _ccxt

    kwargs = _proxy_kwargs()
    if exchange_id == "okx":
        return _ccxt.okx({**kwargs, "options": {"defaultType": "swap"}})
    if exchange_id == "binance":
        return _ccxt.binanceusdm(kwargs)
    if exchange_id == "bybit":
        return _ccxt.bybit({**kwargs, "options": {"defaultType": "linear"}})
    if exchange_id == "gateio":
        return _ccxt.gateio({**kwargs, "options": {"defaultType": "swap"}})
    if exchange_id == "asterdex":
        # Aster 兼容 Binance USDM；优先专用 id，否则改写 urls。
        # [2026-08-04 修复] 此前仅覆盖 fapiPublic/fapiPrivate：binanceusdm 驱动
        # fetch_markets 默认加载 ['spot','linear','inverse']，其中 spot 走 public URL
        # （未覆盖 → 真实 api.binance.com → 被 418/429 限流），且 load_markets 整体失败
        # → P0/P1 采集 100% err（重启后实测 0ok/330err）。修复：完整覆盖全部 API 段为
        # fapi.asterdex.com + 只加载 linear 合约市场。
        # [2026-08-04 API key] 若配置了 ASTERDEX_API_KEY/ASTERDEX_API_SECRET 则注入
        # ccxt 凭证。说明：Asterdex 官方限速基于 IP（2400 weight/min），API key 不提升
        # 公开 K 线配额，但可启用私有数据流（用户数据 WS / 账户查询）等能力。
        for _k in ("apiKey", "secret"):
            _env = os.getenv(f"ASTERDEX_{'API_KEY' if _k == 'apiKey' else 'API_SECRET'}", "").strip()
            if _env:
                kwargs[_k] = _env
        if hasattr(_ccxt, "aster"):
            ex = _ccxt.aster(kwargs)
        else:
            ex = _ccxt.binanceusdm(kwargs)
        try:
            ex.urls["api"] = {
                **(ex.urls.get("api") or {}),
                "fapiPublic": "https://fapi.asterdex.com/fapi/v1",
                "fapiPrivate": "https://fapi.asterdex.com/fapi/v1",
                "fapiPublicV2": "https://fapi.asterdex.com/fapi/v2",
                "fapiPrivateV2": "https://fapi.asterdex.com/fapi/v2",
                "fapiPublicV3": "https://fapi.asterdex.com/fapi/v3",
                "fapiPrivateV3": "https://fapi.asterdex.com/fapi/v3",
                "fapiData": "https://fapi.asterdex.com/futures/data",
                "public": "https://fapi.asterdex.com/api/v3",
                "private": "https://fapi.asterdex.com/api/v3",
                "dapiPublic": "https://fapi.asterdex.com/dapi/v1",
                "dapiPrivate": "https://fapi.asterdex.com/dapi/v1",
                "eapiPublic": "https://fapi.asterdex.com/eapi/v1",
                "eapiPrivate": "https://fapi.asterdex.com/eapi/v1",
                "sapi": "https://fapi.asterdex.com/sapi/v1",
                "papi": "https://fapi.asterdex.com/papi/v1",
            }
            ex.options = {
                **(ex.options or {}),
                "defaultType": "future",
                "fetchMarkets": {"types": ["linear"]},
            }
        except Exception:
            pass
        return ex
    raise ValueError(f"unsupported exchange for sync ccxt: {exchange_id}")


def _make_sync_ccxt(exchange_id: str):
    """线程内复用同步 ccxt，并预加载 markets。"""
    cache: dict = getattr(_tls, "exchanges", None) or {}
    if getattr(_tls, "exchanges", None) is None:
        _tls.exchanges = cache
    ex = cache.get(exchange_id)
    if ex is not None:
        return ex
    ex = _create_sync_ccxt(exchange_id)
    try:
        ex.load_markets()
    except Exception as e:
        logger.warning("[%s] sync ccxt load_markets failed: %s", exchange_id, e)
    cache[exchange_id] = ex
    return ex


def _resolve_ccxt_symbol(ex, exchange_id: str, symbol: str) -> str:
    """优先标准永续符号；若不存在则在已加载 markets 里按 base 回退匹配。"""
    preferred = _ccxt_symbol(exchange_id, symbol)
    markets = getattr(ex, "markets", None) or {}
    if preferred in markets:
        return preferred
    base = (symbol or "").upper().split("-")[0].split("/")[0]
    for mid, m in markets.items():
        if (m.get("base") or "").upper() != base:
            continue
        if m.get("swap") or m.get("future") or (m.get("type") in ("swap", "future")):
            return mid
    return preferred


def _sync_fetch_ohlcv(exchange_id: str, symbol: str, period: str, limit: int = 1,
                      since_ms: int | None = None, bucket: str = "live") -> list:
    """同步拉 OHLCV，可在线程池调用。"""
    if exchange_id == "asterdex":
        # [2026-08-04 修复] 全链限速：P0/P1 实时采集走 live 桶，P2 深历史走
        # backfill 桶，把总速率压在交易所 2400 req/min 之下且互不抢配额，
        # 避免叠加突破导致 IP 级 429 封禁。
        _AsterdexRateLimiter.wait(bucket)
    elif exchange_id in _COLD_EXCHANGES:
        # [2026-08-04 修复] 冷所（备选源）进程级限速：各所独立桶 + 独立冷却，
        # 避免 P1 并发 4 + 深回填叠加时触发冷所自身 429 并连累全链。
        _ColdExchangeRateLimiter.wait(exchange_id)
    try:
        ex = _make_sync_ccxt(exchange_id)
        ccxt_sym = _resolve_ccxt_symbol(ex, exchange_id, symbol)
        if since_ms is not None:
            return ex.fetch_ohlcv(ccxt_sym, period, since=since_ms, limit=limit) or []
        return ex.fetch_ohlcv(ccxt_sym, period, limit=limit) or []
    except Exception as e:
        if exchange_id == "asterdex" and _is_rate_limited_error(e):
            # [2026-08-04 修复] 任一组件命中 429/418 → 置全局封禁。
            # 冷却期内所有 Asterdex 请求在 wait() 阶段直接 fail-fast（不发请求），
            # 实现「一个组件触发限流 → 全链路同步停手」，杜绝自激循环。
            _AsterdexRateLimiter.note_banned()
        elif exchange_id in _COLD_EXCHANGES and _is_rate_limited_error(e):
            # 冷所 429：只冷却该所，不连累 Asterdex 主数据源。
            _ColdExchangeRateLimiter.note_banned(exchange_id)
        raise


class BaseKlineCollector(ABC):
    """K线采集器基类 - 定义统一接口"""

    def __init__(self, exchange_id: str):
        self.exchange_id = exchange_id
        self.logger = logging.getLogger(f"{__name__}.{exchange_id}")

    @abstractmethod
    async def fetch_current_kline(self, symbol: str, period: str = "1m") -> Optional[KlineData]:
        """获取当前分钟的K线数据"""
        pass

    @abstractmethod
    async def fetch_historical_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        period: str = "1m"
    ) -> List[KlineData]:
        """获取历史K线数据"""
        pass

    @abstractmethod
    def get_supported_symbols(self) -> List[str]:
        """获取支持的交易对列表"""
        pass


class HyperliquidKlineCollector(BaseKlineCollector):
    """Hyperliquid K线采集器（含短TTL缓存与请求间隔，防 429）"""

    _symbol_fail_count: Dict[str, int] = {}
    _SYMBOL_FAIL_SUPPRESS_THRESHOLD = 3
    _REQUEST_INTERVAL_SEC = 0.15  # 每次 API 请求后 sleep 150ms

    # 短 TTL 缓存：{(symbol, period): (timestamp, KlineData)}
    _kline_cache: Dict[tuple, tuple] = {}
    _CACHE_TTL_SEC = 60  # 同一 symbol+period 60秒内不重复请求

    def __init__(self):
        super().__init__("hyperliquid")
        # 复用进程内缓存客户端，禁止每次采集 new HyperliquidClient()（会反复 load_markets 拖死 P1）
        from .hyperliquid_market_data import get_hyperliquid_client_for_environment
        self.market_data = get_hyperliquid_client_for_environment("mainnet")

    def _get_cached(self, symbol: str, period: str) -> Optional[KlineData]:
        """检查短 TTL 缓存"""
        import time
        key = (symbol, period)
        cached = self._kline_cache.get(key)
        if cached:
            ts, data = cached
            if time.time() - ts < self._CACHE_TTL_SEC:
                return data
            del self._kline_cache[key]
        return None

    def _put_cache(self, symbol: str, period: str, data: KlineData):
        import time
        self._kline_cache[(symbol, period)] = (time.time(), data)

    async def _throttle(self):
        """请求间隔控制，减少 429"""
        import asyncio
        await asyncio.sleep(self._REQUEST_INTERVAL_SEC)

    async def fetch_current_kline(self, symbol: str, period: str = "1m") -> Optional[KlineData]:
        """获取当前分钟K线（含缓存和限频）"""
        fail_cnt = self._symbol_fail_count.get(symbol, 0)
        if fail_cnt >= self._SYMBOL_FAIL_SUPPRESS_THRESHOLD:
            return None

        cached = self._get_cached(symbol, period)
        if cached:
            return cached

        try:
            await self._throttle()
            # persist=False：入库由 realtime collector 统一做，避免双写拖慢热路径
            klines = await run_kline_io(
                self.market_data.get_kline_data, symbol, period, 1, False,
            )
            if not klines:
                return None

            self._symbol_fail_count.pop(symbol, None)
            latest = klines[0]
            result = KlineData(
                exchange=self.exchange_id,
                symbol=symbol,
                timestamp=int(latest['timestamp']),
                period=period,
                open_price=float(latest['open']),
                high_price=float(latest['high']),
                low_price=float(latest['low']),
                close_price=float(latest['close']),
                volume=float(latest['volume'])
            )
            self._put_cache(symbol, period, result)
            return result
        except Exception as e:
            self._symbol_fail_count[symbol] = fail_cnt + 1
            if self._symbol_fail_count[symbol] == self._SYMBOL_FAIL_SUPPRESS_THRESHOLD:
                self.logger.warning(
                    f"Symbol {symbol} failed {self._SYMBOL_FAIL_SUPPRESS_THRESHOLD} times, "
                    f"suppressing future requests. Error: {e}")
            else:
                self.logger.error(f"Failed to fetch current kline for {symbol}: {e}")
            return None

    async def fetch_historical_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        period: str = "1m"
    ) -> List[KlineData]:
        """获取历史K线数据（含限频 + 分页 since）。

        根因修复：之前只取最近 N 根再按时间过滤 → 请求 2024 数据实际拉 2026 的，
        全被过滤掉返回 0。现在用 ccxt since 参数从 start_time 真正往前拉，
        分页直到覆盖整个 [start_time, end_time] 范围。
        """
        try:
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            else:
                start_time = start_time.astimezone(timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            else:
                end_time = end_time.astimezone(timezone.utc)

            period_sec = {"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,
                          "1h":3600,"2h":7200,"4h":14400,"8h":28800,
                          "12h":43200,"1d":86400,"1w":604800,"1M":2592000}.get(period, 3600)
            batch_size = 5000  # 每批最多 5000 根
            total_sec = (end_time - start_time).total_seconds()
            total_expected = int(total_sec / period_sec)

            result: List[KlineData] = []
            cursor = start_time  # 从起始时间往前拉

            while cursor < end_time:
                batch_limit = min(batch_size, int((end_time - cursor).total_seconds() / period_sec) + 1)
                if batch_limit < 1:
                    break

                since_ms = int(cursor.timestamp() * 1000)
                await self._throttle()
                klines = await run_kline_io(
                    self.market_data.get_kline_data,
                    symbol, period, batch_limit, False, since=since_ms,
                )

                if not klines:
                    break  # API 无更多数据

                batch_added = 0
                for kline in klines:
                    try:
                        kline_time = datetime.fromtimestamp(kline['timestamp'], tz=timezone.utc)
                        if start_time <= kline_time <= end_time:
                            result.append(KlineData(
                                exchange=self.exchange_id,
                                symbol=symbol,
                                timestamp=int(kline['timestamp']),
                                period=period,
                                open_price=float(kline['open']),
                                high_price=float(kline['high']),
                                low_price=float(kline['low']),
                                close_price=float(kline['close']),
                                volume=float(kline['volume'])
                            ))
                            batch_added += 1
                    except (TypeError, ValueError, KeyError) as row_err:
                        self.logger.debug(f"Skipping invalid historical kline for {symbol}: {row_err}")

                # 推进游标到最后一条 K线时间 + 1 周期
                last_ts = max(k['timestamp'] for k in klines)
                cursor = datetime.fromtimestamp(last_ts, tz=timezone.utc) + timedelta(seconds=period_sec)

                if batch_added == 0:
                    break  # 无新数据，防死循环

            self.logger.info(f"[Historical] {symbol}/{period}: fetched {len(result)}/{total_expected} roots")
            return result
        except Exception as e:
            self.logger.error(f"Failed to fetch historical klines for {symbol}: {e}")
            return []

    def get_supported_symbols(self) -> List[str]:
        """获取用户配置的交易对（实时采集用）。

        [2026-07-30] 统一从 DB user_trading_pairs 读取，不再调 hyperliquid_symbol_service。
        """
        try:
            from backend.services.trading_pairs_config import get_user_trading_pairs
            pairs = get_user_trading_pairs()
            if pairs:
                return [s.upper() for s in pairs]
        except Exception as e:
            self.logger.warning(f"Failed to get user trading pairs: {e}")

        return []


class CcxtCompatibleKlineCollector(BaseKlineCollector):
    """Binance / Asterdex / OKX 等 — 同步 ccxt 拉取（不走 async adapter，避免跨 loop）。"""

    # [2026-08-04 修复] 请求间隔可配：Asterdex 全站共享 2400 req/min 上限，
    # P2 历史回填批量拉取时 0.2s/批会让回填与 P0 叠加超限。默认 0.3s，可用
    # KLINE_REQUEST_INTERVAL_SEC 覆盖（如 0.5 进一步降速）。
    _REQUEST_INTERVAL_SEC = float(os.getenv("KLINE_REQUEST_INTERVAL_SEC", "0.3"))
    # [2026-08-04 修复] 深历史回填更慢间隔：P2 走独立 backfill 限速桶
    # （默认 150 req/min）+ 1.2s/请求，慢速渐进且不抢 P0/P1 的 live 配额。
    _BACKFILL_INTERVAL_SEC = float(os.getenv("KLINE_BACKFILL_REQUEST_INTERVAL_SEC", "1.2"))

    async def _throttle(self):
        await asyncio.sleep(self._REQUEST_INTERVAL_SEC)

    async def fetch_current_kline(self, symbol: str, period: str = "1m") -> Optional[KlineData]:
        try:
            await self._throttle()
            ohlcv = await run_kline_io(
                _sync_fetch_ohlcv, self.exchange_id, symbol.upper(), period, 2,
            )
            if not ohlcv:
                return None
            candle = ohlcv[-1]
            ts = int(candle[0] / 1000)
            return KlineData(
                exchange=self.exchange_id,
                symbol=symbol.upper().split("-")[0].split("/")[0],
                timestamp=ts,
                period=period,
                open_price=float(candle[1]),
                high_price=float(candle[2]),
                low_price=float(candle[3]),
                close_price=float(candle[4]),
                volume=float(candle[5] or 0),
            )
        except Exception as e:
            if isinstance(e, ExchangeRateLimitError) or _is_rate_limited_error(e):
                # [2026-08-04 修复] 限流必须上抛：上层据此进入冷却，停止继续顶满窗口。
                raise ExchangeRateLimitError(
                    f"[{self.exchange_id}] {symbol}/{period} 限流/封禁: {e}"
                ) from e
            self.logger.error(f"Failed to fetch current kline for {symbol} on {self.exchange_id}: {e}")
            return None

    async def fetch_historical_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        period: str = "1m",
    ) -> List[KlineData]:
        """获取历史K线（since 分页 + 同步 ccxt）。"""
        try:
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            else:
                start_time = start_time.astimezone(timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            else:
                end_time = end_time.astimezone(timezone.utc)

            period_sec = {"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,
                          "1h":3600,"2h":7200,"4h":14400,"8h":28800,
                          "12h":43200,"1d":86400,"1w":604800,"1M":2592000}.get(period, 3600)
            batch_size = 1000
            result: List[KlineData] = []
            cursor = start_time
            _max_consecutive_failures = 3
            _consecutive_failures = 0
            base_sym = symbol.upper().split("-")[0].split("/")[0]

            while cursor < end_time:
                since_ms = int(cursor.timestamp() * 1000)
                # [2026-08-04 修复] 深历史回填走 backfill 桶 + 更慢间隔。
                await asyncio.sleep(self._BACKFILL_INTERVAL_SEC)
                try:
                    ohlcv = await run_kline_io(
                        _sync_fetch_ohlcv,
                        self.exchange_id, base_sym, period, batch_size, since_ms,
                        "backfill",
                    )
                    _consecutive_failures = 0
                except Exception as e:
                    if isinstance(e, ExchangeRateLimitError) or _is_rate_limited_error(e):
                        # [2026-08-04 修复] 限流立即中止回填并上抛，交给上层冷却。
                        raise ExchangeRateLimitError(
                            f"[{self.exchange_id}] {base_sym}/{period} 历史回填限流: {e}"
                        ) from e
                    self.logger.warning(
                        f"[{self.exchange_id}] {base_sym}/{period} batch at {cursor.date()} failed: {e}"
                    )
                    _consecutive_failures += 1
                    if _consecutive_failures >= _max_consecutive_failures:
                        self.logger.info(
                            f"[{self.exchange_id}] {base_sym}/{period}: 连续失败 "
                            f"{_consecutive_failures} 次，跳过"
                        )
                        break
                    cursor = cursor + timedelta(seconds=period_sec * batch_size)
                    continue

                if not ohlcv:
                    break

                batch_added = 0
                for candle in ohlcv:
                    ts = int(candle[0] / 1000)
                    kline_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if start_time <= kline_time <= end_time and candle[1]:
                        result.append(KlineData(
                            exchange=self.exchange_id,
                            symbol=base_sym,
                            timestamp=ts, period=period,
                            open_price=float(candle[1]), high_price=float(candle[2]),
                            low_price=float(candle[3]), close_price=float(candle[4]),
                            volume=float(candle[5] or 0),
                        ))
                        batch_added += 1

                last_ts = max(c[0] for c in ohlcv) / 1000
                cursor = datetime.fromtimestamp(last_ts, tz=timezone.utc) + timedelta(seconds=period_sec)
                if batch_added == 0:
                    break

            self.logger.info(
                f"[Historical] {self.exchange_id} {base_sym}/{period}: fetched {len(result)} roots"
            )
            return result
        except Exception as e:
            if isinstance(e, ExchangeRateLimitError) or _is_rate_limited_error(e):
                raise ExchangeRateLimitError(
                    f"[{self.exchange_id}] {symbol}/{period} 历史回填限流: {e}"
                ) from e
            self.logger.error(f"Failed to fetch historical klines for {symbol} on {self.exchange_id}: {e}")
            return []

    def get_supported_symbols(self) -> List[str]:
        """从当前交易所动态获取支持的 symbol 列表。"""
        try:
            ex = _make_sync_ccxt(self.exchange_id)
            markets = ex.load_markets()
            exchange_symbols = set()
            for m in markets.values():
                base = (m.get("base") or "").upper()
                if base and (m.get("swap") or m.get("future") or m.get("spot")):
                    exchange_symbols.add(base)

            if not exchange_symbols:
                self.logger.warning(f"[{self.exchange_id}] markets 为空")
                return []

            watchlist = set()
            try:
                from backend.services.trading_pairs_config import get_user_trading_pairs
                watchlist = set(s.upper() for s in (get_user_trading_pairs() or []))
            except Exception:
                pass

            if watchlist:
                usable = sorted(watchlist & exchange_symbols)
                skipped = sorted(watchlist - exchange_symbols)
                if skipped:
                    self.logger.info(
                        f"[{self.exchange_id}] 用户配置中 {len(skipped)} 个 symbol 不支持: "
                        f"{skipped[:10]}，已跳过"
                    )
                if usable:
                    return usable

            return sorted(exchange_symbols)[:20]

        except Exception as e:
            self.logger.warning(f"[{self.exchange_id}] 获取 symbol 失败: {e}")
            return []


class BinanceKlineCollector(CcxtCompatibleKlineCollector):
    def __init__(self):
        super().__init__("binance")


class AsterdexKlineCollector(CcxtCompatibleKlineCollector):
    def __init__(self):
        super().__init__("asterdex")


class AsterKlineCollector(AsterdexKlineCollector):
    """兼容旧 exchange id `aster`。"""


class BybitKlineCollector(CcxtCompatibleKlineCollector):
    def __init__(self):
        super().__init__("bybit")


class OkxKlineCollector(CcxtCompatibleKlineCollector):
    def __init__(self):
        super().__init__("okx")


class GateioKlineCollector(CcxtCompatibleKlineCollector):
    def __init__(self):
        super().__init__("gateio")


class ExchangeDataSourceFactory:
    """交易所数据源工厂 - 根据配置返回对应采集器"""

    _collectors = {
        "hyperliquid": HyperliquidKlineCollector,
        "binance": BinanceKlineCollector,
        "asterdex": AsterdexKlineCollector,
        "aster": AsterKlineCollector,
        "bybit": BybitKlineCollector,
        "okx": OkxKlineCollector,
        "gateio": GateioKlineCollector,
    }
    _instances: Dict[str, BaseKlineCollector] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def get_collector(cls, exchange_id: str) -> BaseKlineCollector:
        """根据交易所ID获取对应的采集器实例（进程内单例，避免反复初始化）。"""
        from backend.services.market_data_adapters.registry import ExchangeAdapterRegistry

        exchange_id = ExchangeAdapterRegistry.normalize_exchange(exchange_id)
        if exchange_id not in cls._collectors:
            raise ValueError(f"Unsupported exchange: {exchange_id}")

        with cls._instances_lock:
            inst = cls._instances.get(exchange_id)
            if inst is None:
                inst = cls._collectors[exchange_id]()
                cls._instances[exchange_id] = inst
            return inst

    @classmethod
    def get_supported_exchanges(cls) -> List[str]:
        """获取支持的交易所列表"""
        return list(cls._collectors.keys())
