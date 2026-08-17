"""
统一市场数据中心 — UnifiedMarketDataCenter（数据中台整改核心）。

═══════════════════════════════════════════════════════════════════════
  所有市场数据读取的唯一入口。禁止业务层绕开此处直连 DB/交易所。
═══════════════════════════════════════════════════════════════════════

背景（审计结论）：
    系统曾有 13 个 K线入口、11 个价格入口、~40 个文件各自直连 DB/交易所，
    4 个"数据中心"类（DataHub/UnifiedDataPool/MarketDataHub/kline_data_service）
    互相不知道谁管什么。本类收口全部数据读取。

核心能力（取代散落的入口）：
    1. get_klines()         — K线（多交易所择优 + 历史范围 + 实时）
    2. get_price()          — 实时价格（多交易所对比）
    3. get_orderbook()      — 盘口（多交易所聚合）
    4. get_derivatives()    — 衍生品（funding/OI/basis）
    5. get_coverage()       — 数据覆盖报告（多交易所）
    6. get_snapshot()       — 统一快照（一次性取全量）

设计原则：
    - 多交易所择优：同一品种查所有所，取数据最深/最新的
    - 标注来源：每条数据带 source_exchange，可追溯
    - 统一缓存：TTL 分层（价格 1.5s / K线 60s / 衍生品 600s）
    - 统一降级：主所 down → 自动 fallback 其他所
    - 单例：全局唯一实例，所有调用方共享缓存

迁移策略：
    现有 kline_data_service / DataHub / UnifiedDataPool 的方法
    逐步委托到本类；存量调用方逐步改为 from ... import data_center。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.services.symbol_normalizer import normalize_symbol

import pandas as pd

logger = logging.getLogger(__name__)

# 所有已知交易所（择优时遍历）
ALL_EXCHANGES: list[str] = ["hyperliquid", "asterdex", "binance", "bybit", "okx"]

# 周期 → 秒
PERIOD_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000,
}

# 秒级 ticker 最大可接受年龄（asterdex ticker poller 2s 通道，5s 内视为新鲜）
TICKER_MAX_AGE_SEC: float = 5.0

# 跨进程 DC ticker 拉取缓存（TTL 1s，防每请求都打 DC HTTP）
_DC_TICKER_CACHE: dict = {}
_DC_TICKER_CACHE_TTL_SEC: float = 1.0
_DC_TICKER_URL_BASE: str = os.getenv("DATA_CENTER_TICKER_URL", "http://127.0.0.1:9100").rstrip("/")


@dataclass
class KlineResult:
    """K线查询结果（标注来源）。"""
    symbol: str
    period: str
    exchange: str                # 数据来源交易所
    rows: list[dict]             # [{"timestamp","open","high","low","close","volume"}]
    count: int = 0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    stale_sec: Optional[float] = None  # 最新一根相对现在的滞后秒数
    purpose: str = "research"          # trade | research
    closed_only: bool = False          # [P0-5] True=已剔除未收盘 forming bar

    def __post_init__(self):
        self.count = len(self.rows)
        if self.rows:
            self.first_ts = self.rows[0]["timestamp"]
            self.last_ts = self.rows[-1]["timestamp"]
            try:
                last = int(self.last_ts or 0)
                if last > 1e12:
                    last = int(last / 1000)
                if last > 0:
                    self.stale_sec = max(0.0, time.time() - last)
            except Exception:
                self.stale_sec = None

    @property
    def is_fresh(self) -> bool:
        """是否满足交易用途新鲜度（按周期动态阈值）。"""
        if self.stale_sec is None:
            return False
        period_sec = PERIOD_SECONDS.get(self.period, 300)
        # 允许最多 2 根周期 + 60s 缓冲；过期则不可用于决策
        return self.stale_sec <= (period_sec * 2 + 60)

    def to_dataframe(self) -> pd.DataFrame:
        """转 DataFrame（index=datetime, OHLCV 列）。"""
        if not self.rows:
            return pd.DataFrame()
        df = pd.DataFrame(self.rows)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.set_index("datetime").sort_index()
        return df[["open", "high", "low", "close", "volume"]]


@dataclass
class CoverageReport:
    """数据覆盖报告（多交易所）。"""
    symbol: str
    period: str
    by_exchange: dict[str, dict] = field(default_factory=dict)  # {exchange: {count, first_ts, last_ts, years}}
    best_exchange: str = ""
    best_count: int = 0
    best_years: float = 0.0

    @property
    def is_research_ready(self) -> bool:
        """数据是否够做因子研究（≥2 年）。"""
        return self.best_years >= 2.0


class UnifiedMarketDataCenter:
    """
    统一市场数据中心（单例）。

    所有市场数据读取的唯一入口。
    用法：
        from backend.services.data_center import data_center
        klines = data_center.get_klines("BTC", "1d", start="2023-01-01")
        price = data_center.get_price("BTC")
        cov = data_center.get_coverage("BTC", "1d")
    """

    _instance: Optional["UnifiedMarketDataCenter"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        # 统一缓存（分层 TTL）
        self._cache: dict[tuple, dict] = {}  # {key: {"ts": float, "data": Any}}
        self._ttl_price = 1.5       # 价格 1.5s
        self._ttl_kline = 60.0      # K线 60s
        self._ttl_kline_history = 300.0  # 历史 K线 5 分钟（不常变）
        self._ttl_coverage = 600.0  # 覆盖报告 10 分钟
        self._cache_lock = threading.Lock()
        logger.info("[DataCenter] 统一市场数据中心初始化完成 — 所有数据读取的唯一入口")

    # ================================================================
    #  K线查询（核心）
    # ================================================================

    def get_klines(
        self,
        symbol: str,
        period: str = "1d",
        count: int = 0,
        start: str | int | datetime | None = None,
        end: str | int | datetime | None = None,
        exchange: str | None = None,
        purpose: str = "trade",
        closed_only: Optional[bool] = None,
    ) -> KlineResult:
        """
        K线查询 — 唯一业务读入口。

        purpose:
            "trade"（默认）— 强制使用当前决策交易所（会话 active_exchange /
                DEFAULT_EXCHANGE）。调用方传入的其他 exchange **无效**，禁止
                「交易 A 所、数据 B 所」；也不再按「行数最多」跨所择优。
            "research" — 允许指定 exchange；未指定时才多所择优（仅研究/对比）。

        closed_only:
            [P0-5 前视隔离] None（默认）→ trade 用途 True / research 用途 False；
            True 时剔除当前未收盘的 forming bar（timestamp+period > now），
            避免指标/因子/信号基于仍在跳动的 close 计算（实盘侧活前视）。
        """
        symbol = symbol.upper().split("-")[0].split("/")[0]  # BTC-PERP → BTC
        start_ts = self._to_ts(start) if start else 0
        end_ts = self._to_ts(end) if end else 0
        purpose_l = (purpose or "trade").strip().lower()
        if purpose_l not in ("trade", "research"):
            purpose_l = "trade"

        # [P0-5] closed_only 默认按用途：trade→True（决策不吃未收盘 bar），research→False
        if closed_only is None:
            closed_only = (purpose_l == "trade")

        if purpose_l == "trade":
            from backend.services.exchange_config import get_active_exchange
            decision_ex = (get_active_exchange() or "asterdex").strip().lower()
            if decision_ex == "aster":
                decision_ex = "asterdex"
            req = (exchange or "").strip().lower()
            if req == "aster":
                req = "asterdex"
            if req and req != decision_ex:
                logger.warning(
                    "[DataCenter] purpose=trade 拒绝跨所读取 %s→%s，强制 %s (%s/%s)",
                    req, decision_ex, decision_ex, symbol, period,
                )
            exchange = decision_ex

        # 缓存：trade 带交易所键，避免串源；closed_only 纳入键（口径不同不可混用缓存）
        cache_key = ("klines", purpose_l, exchange or "", symbol, period, count, start_ts, end_ts, closed_only)
        cached = self._get_cache(cache_key, self._ttl_kline_history if start_ts else self._ttl_kline)
        if cached is not None:
            return cached

        if purpose_l == "trade":
            exchanges = [exchange] if exchange else ["asterdex"]
            best = self._query_best_exchange(symbol, period, count, start_ts, end_ts, exchanges, closed_only=closed_only)
            best.purpose = "trade"
            # 交易用途：过期不静默换所，返回空结果（调用方应拒开仓）
            if best.count > 0 and not best.is_fresh:
                logger.warning(
                    "[DataCenter] %s/%s@%s 数据过期 stale=%.0fs，trade 用途返回不可用",
                    symbol, period, best.exchange, best.stale_sec or -1,
                )
                best = KlineResult(symbol=symbol, period=period, exchange=exchange or "", rows=[], purpose="trade")
        else:
            exchanges = [exchange] if exchange else ALL_EXCHANGES
            best = self._query_best_exchange(symbol, period, count, start_ts, end_ts, exchanges, closed_only=closed_only)
            best.purpose = "research"

        if best.count > 0:
            self._set_cache(cache_key, best)
        return best

    def get_klines_batch(
        self,
        symbols: list[str],
        period: str,
        count: int = 500,
        exchange: str | None = None,
        purpose: str = "trade",
    ) -> dict[str, KlineResult]:
        """批量 K线（唯一批量入口）。trade 用途强制 active_exchange。"""
        out: dict[str, KlineResult] = {}
        for sym in symbols or []:
            su = (sym or "").upper().strip()
            if not su or su in out:
                continue
            out[su] = self.get_klines(su, period, count=count, exchange=exchange, purpose=purpose)
        return out

    def get_klines_df(
        self, symbol: str, period: str = "1d", **kw
    ) -> pd.DataFrame:
        """便捷：直接返回 DataFrame。"""
        result = self.get_klines(symbol, period, **kw)
        return result.to_dataframe()

    def _query_best_exchange(
        self, symbol: str, period: str, count: int,
        start_ts: int, end_ts: int, exchanges: list[str],
        closed_only: bool = False,
    ) -> KlineResult:
        """遍历交易所，取数据最深的。

        [P0-5] closed_only=True 时剔除未收盘 forming bar：
        该 bar 的 close 仍在跳动，指标/因子/信号消费它 = 实盘侧活前视，
        与回测（只吃已收盘 bar）口径不一致。
        """
        best_rows: list[dict] = []
        best_exchange = ""
        best_count = 0
        _now = int(time.time())
        _period_sec = PERIOD_SECONDS.get(period, 300)

        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
        except Exception:
            return KlineResult(symbol, period, "", [])

        try:
            db = MarketSessionLocal()
            try:
                for ex in exchanges:
                    try:
                        conditions = "exchange = :ex AND symbol = :sym AND period = :p"
                        params: dict = {"ex": ex, "sym": symbol, "p": period}
                        if start_ts:
                            conditions += " AND timestamp >= :start"
                            params["start"] = start_ts
                        if end_ts:
                            conditions += " AND timestamp <= :end"
                            params["end"] = end_ts

                        order = "ORDER BY timestamp DESC LIMIT :limit" if count else "ORDER BY timestamp"
                        if count:
                            params["limit"] = count

                        rows = db.execute(sa_text(
                            f"SELECT timestamp, open_price, high_price, low_price, close_price, volume "
                            f"FROM crypto_klines WHERE {conditions} {order}"
                        ), params).fetchall()

                        if len(rows) > best_count:
                            best_count = len(rows)
                            best_exchange = ex
                            # count 模式是 DESC → 反转
                            if count:
                                rows = list(reversed(rows))
                            if closed_only:
                                # [P0-5] 剔除 forming bar：开K时间 + 周期 > 当前时刻 = 未收盘
                                rows = [
                                    r for r in rows
                                    if r[0] is not None and int(r[0]) + _period_sec <= _now
                                ]
                            best_rows = [{
                                "timestamp": r[0],
                                "datetime": datetime.fromtimestamp(int(r[0]), tz=timezone.utc).isoformat(),
                                "open": float(r[1] or 0), "high": float(r[2] or 0),
                                "low": float(r[3] or 0), "close": float(r[4] or 0),
                                "volume": float(r[5] or 0),
                            } for r in rows if r[0] is not None and r[1] is not None]
                    except Exception:
                        continue
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataCenter] kline query error: {e}")

        if best_count > 0:
            logger.debug(f"[DataCenter] {symbol}/{period}: best={best_exchange} ({best_count} roots, closed_only={closed_only})")
        return KlineResult(symbol=symbol, period=period, exchange=best_exchange, rows=best_rows, closed_only=closed_only)

    # ================================================================
    #  实时价格（多交易所对比）
    # ================================================================

    def get_price(self, symbol: str, exchange: str | None = None, purpose: str = "trade") -> float:
        """实时价格（统一入口）。trade 用途强制 active_exchange，禁止跨所静默回退。

        [2026-08-07 价格权威口径] 内部走 get_price_with_ts：
        秒级 ticker（poller/hub，带 stale 校验）优先 → DB 最新 1m close 兜底。
        """
        result = self.get_price_with_ts(symbol, exchange, purpose)
        if result:
            return float(result[0])
        # 历史兜底：research 用途下尝试统一数据池快照（主进程内存）
        purpose_l = (purpose or "trade").strip().lower()
        if purpose_l != "trade":
            try:
                from backend.services.unified_data_pool import unified_data_pool
                snap = unified_data_pool.get_snapshot(max_age=10)
                base = normalize_symbol(symbol)
                if snap and base and base in snap.markets:
                    return float(snap.markets[base].price or 0)
            except Exception:
                pass
        return 0.0

    def get_price_with_ts(self, symbol: str, exchange: str | None = None, purpose: str = "trade") -> Optional[tuple]:
        """权威价格链路（单一真相）：返回 (price, ts) 或 None。

        顺序（同一 exchange 下）：
        1) 秒级 ticker — DC 内 asterdex ticker poller（2s 全市场通道）
        2) hub ticker — 带 stale 校验（默认 5s）
        3) DB 最新 1m close — 分钟级兜底，返回该根 K 线 timestamp

        ts 供调用方判断新鲜度；trade 用途强制 active_exchange。
        """
        base = normalize_symbol(symbol)
        if not base:
            return None
        purpose_l = (purpose or "trade").strip().lower()
        if purpose_l == "trade":
            from backend.services.exchange_config import get_active_exchange
            decision_ex = (get_active_exchange() or "asterdex").strip().lower()
            if decision_ex == "aster":
                decision_ex = "asterdex"
            exchange = decision_ex
        ex = (exchange or "").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        if not ex:
            ex = "asterdex"

        cache_key = ("price_ts", purpose_l, base, ex)
        cached = self._get_cache(cache_key, self._ttl_price)
        if cached is not None:
            return cached

        price, ts = self._ticker_price_with_ts(base, ex)
        if not price or price <= 0:
            price, ts = self._db_1m_price_with_ts(base, ex)
        if not price or price <= 0:
            return None
        result = (float(price), float(ts or 0))
        self._set_cache(cache_key, result)
        return result

    def _dc_ticker_price_with_ts(self, base: str) -> tuple:
        """跨进程秒级 ticker：从数据中心进程 :9100 拉 2s 全市场最新价。

        带 1s TTL 内存缓存与 1.5s 硬超时，DC 不可达时静默降级（由调用方走 DB 兜底）。
        """
        try:
            now = time.time()
            cached = _DC_TICKER_CACHE.get(base)
            if cached and now - cached[2] <= _DC_TICKER_CACHE_TTL_SEC:
                return (float(cached[0]), float(cached[1]))
            url = f"{_DC_TICKER_URL_BASE}/ticker/{base}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            # 本机回环直连：绕过 .env 注入的 HTTP(S)_PROXY（1080 代理不转发 127.0.0.1）
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            price = float(data.get("price") or 0)
            ts = float(data.get("ts") or 0)
            if price > 0 and ts > 0:
                _DC_TICKER_CACHE[base] = (price, ts, now)
                return (float(price), float(ts))
        except Exception:
            pass
        return (None, None)

    def _ticker_price_with_ts(self, base: str, ex: str) -> tuple:
        """秒级 ticker：跨进程 DC REST（2s 通道）→ 本进程 poller → hub ticker（stale 校验）。"""
        if ex == "asterdex":
            # 0) 跨进程 DC ticker（DC_ONLY 下 backend 无 poller，此为秒级主通道）
            price, ts = self._dc_ticker_price_with_ts(base)
            if price and price > 0 and ts and time.time() - float(ts) <= TICKER_MAX_AGE_SEC:
                return (float(price), float(ts))
            try:
                from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                entry = asterdex_ticker_poller.get_price_with_ts(base)
                if entry and entry[0] and entry[0] > 0:
                    if time.time() - float(entry[1]) <= TICKER_MAX_AGE_SEC:
                        return (float(entry[0]), float(entry[1]))
            except Exception:
                pass
        try:
            from backend.services.market_data_hub import market_data_hub
            entry = market_data_hub.get_ticker_with_ts(ex, base)
            if entry:
                return (float(entry[0]), float(entry[1]))
        except Exception:
            pass
        return (None, None)

    def _db_1m_price_with_ts(self, base: str, ex: str) -> tuple:
        """DB 最新 1m close 兜底（分钟级），返回 (close, timestamp)。

        [2026-08-15 P0-3 修复] 增加 stale 门：1m bar 开盘时间距今超过
        3 根周期 + 30s 缓冲即视为过期返回 None，防止采集停摆时静默用旧价。
        """
        try:
            from sqlalchemy import text as sa_text
            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                row = db.execute(
                    sa_text(
                        """
                        SELECT close_price, timestamp FROM crypto_klines
                        WHERE exchange = :ex AND symbol = :sym AND period = '1m' AND close_price > 0
                        ORDER BY timestamp DESC LIMIT 1
                        """
                    ),
                    {"ex": ex, "sym": base},
                ).first()
            if row and row[0] and float(row[0]) > 0:
                ts = float(row[1] or 0)
                age_sec = time.time() - ts
                if age_sec > (3 * 60 + 30):
                    logger.warning(
                        "[DataCenter] %s 1m close 兜底过期 age=%.0fs，拒绝返回", base, age_sec,
                    )
                    return (None, None)
                return (float(row[0]), ts)
        except Exception:
            pass
        return (None, None)

    # ================================================================
    #  衍生品 / 盘口 / 统一快照（[2026-08-15 E5] 补齐 docstring 声明）
    # ================================================================

    def get_derivatives(self, symbol: str, exchange: str | None = None) -> dict:
        """衍生品快照（funding/OI/mark/oracle/mid/premium/24h 名义量）。

        数据源：market_asset_metrics 最新行（毫秒时间戳）；缺行时用
        perp_funding 补 funding_rate。purpose=trade 语义下强制 active_exchange。
        缓存 30s（衍生品慢变量）。无数据返回空 dict（诚实，不造数）。
        """
        from sqlalchemy import text as sa_text

        from backend.database.connection import MarketSessionLocal
        from backend.services.exchange_config import get_active_exchange

        base = normalize_symbol(symbol)
        if not base:
            return {}
        ex = (exchange or get_active_exchange() or "asterdex").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        cache_key = ("derivatives", base, ex)
        cached = self._get_cache(cache_key, 30.0)
        if cached is not None:
            return cached

        out: dict = {}
        try:
            with MarketSessionLocal() as db:
                row = db.execute(
                    sa_text(
                        "SELECT open_interest, funding_rate, mark_price, oracle_price, "
                        "mid_price, premium, day_notional_volume "
                        "FROM market_asset_metrics "
                        "WHERE exchange=:ex AND symbol=:sym "
                        "ORDER BY timestamp DESC LIMIT 1"
                    ),
                    {"ex": ex, "sym": base},
                ).first()
            if row:
                out = {
                    "exchange": ex,
                    "symbol": base,
                    "open_interest": float(row[0]) if row[0] is not None else None,
                    "funding_rate": float(row[1]) if row[1] is not None else None,
                    "mark_price": float(row[2]) if row[2] is not None else None,
                    "oracle_price": float(row[3]) if row[3] is not None else None,
                    "mid_price": float(row[4]) if row[4] is not None else None,
                    "premium": float(row[5]) if row[5] is not None else None,
                    "day_notional_volume": float(row[6]) if row[6] is not None else None,
                }
            if out.get("funding_rate") is None:
                try:
                    with MarketSessionLocal() as db:
                        fr = db.execute(
                            sa_text(
                                "SELECT funding_rate FROM perp_funding "
                                "WHERE exchange=:ex AND symbol=:sym "
                                "ORDER BY timestamp DESC LIMIT 1"
                            ),
                            {"ex": ex, "sym": base},
                        ).scalar()
                    if fr is not None:
                        out["funding_rate"] = float(fr)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[DataCenter] get_derivatives {base}@{ex} 失败: {e}")
        if out:
            self._set_cache(cache_key, out)
        return out

    def get_orderbook(self, symbol: str, exchange: str | None = None) -> dict:
        """盘口快照（best bid/ask、spread、5/10 档深度、挂单数、raw_levels）。

        数据源：market_orderbook_snapshots 最新行（毫秒时间戳）。缓存 5s。
        无数据返回空 dict。"""
        from sqlalchemy import text as sa_text

        from backend.database.connection import MarketSessionLocal
        from backend.services.exchange_config import get_active_exchange

        base = normalize_symbol(symbol)
        if not base:
            return {}
        ex = (exchange or get_active_exchange() or "asterdex").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        cache_key = ("orderbook", base, ex)
        cached = self._get_cache(cache_key, 5.0)
        if cached is not None:
            return cached

        out: dict = {}
        try:
            with MarketSessionLocal() as db:
                row = db.execute(
                    sa_text(
                        "SELECT best_bid, best_ask, spread, bid_depth_5, ask_depth_5, "
                        "bid_depth_10, ask_depth_10, bid_orders_count, ask_orders_count, "
                        "raw_levels, timestamp "
                        "FROM market_orderbook_snapshots "
                        "WHERE exchange=:ex AND symbol=:sym "
                        "ORDER BY timestamp DESC LIMIT 1"
                    ),
                    {"ex": ex, "sym": base},
                ).first()
            if row and row[0] is not None:
                out = {
                    "exchange": ex,
                    "symbol": base,
                    "best_bid": float(row[0]),
                    "best_ask": float(row[1]) if row[1] is not None else None,
                    "spread": float(row[2]) if row[2] is not None else None,
                    "bid_depth_5": float(row[3]) if row[3] is not None else None,
                    "ask_depth_5": float(row[4]) if row[4] is not None else None,
                    "bid_depth_10": float(row[5]) if row[5] is not None else None,
                    "ask_depth_10": float(row[6]) if row[6] is not None else None,
                    "bid_orders_count": int(row[7] or 0),
                    "ask_orders_count": int(row[8] or 0),
                    "raw_levels": row[9],
                    "ts_ms": int(row[10] or 0),
                }
        except Exception as e:
            logger.debug(f"[DataCenter] get_orderbook {base}@{ex} 失败: {e}")
        if out:
            self._set_cache(cache_key, out)
        return out

    def get_snapshot(self, symbol: str, exchange: str | None = None) -> dict:
        """统一快照：价格(秒级 ticker→1m 兜底) + 衍生品 + 盘口 + 最新 1d K 线。

        一次调用取全量，供研究/仪表盘；缓存 5s。各子块缺失时对应键为 None/空，
        绝不造数。"""
        base = normalize_symbol(symbol)
        if not base:
            return {}
        ex = (exchange or "asterdex").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        cache_key = ("snapshot", base, ex)
        cached = self._get_cache(cache_key, 5.0)
        if cached is not None:
            return cached

        price_result = self.get_price_with_ts(base, ex, purpose="research")
        snap: dict = {
            "symbol": base,
            "exchange": ex,
            "price": float(price_result[0]) if price_result else None,
            "price_ts": float(price_result[1]) if price_result else None,
            "derivatives": self.get_derivatives(base, ex),
            "orderbook": self.get_orderbook(base, ex),
            "klines_1d": {},
        }
        try:
            kr = self.get_klines(base, "1d", count=2, exchange=ex, purpose="research")
            if kr.rows:
                last = kr.rows[-1]
                snap["klines_1d"] = {
                    "close": float(last.get("close") or 0),
                    "volume": float(last.get("volume") or 0),
                    "timestamp": last.get("timestamp"),
                }
        except Exception:
            pass
        self._set_cache(cache_key, snap)
        return snap

    # ================================================================
    #  数据覆盖报告
    # ================================================================

    def get_coverage(self, symbol: str, period: str = "1d") -> CoverageReport:
        """多交易所数据覆盖报告。"""
        symbol = symbol.upper().split("-")[0].split("/")[0]
        cache_key = ("coverage", symbol, period)
        cached = self._get_cache(cache_key, self._ttl_coverage)
        if cached is not None:
            return cached

        report = CoverageReport(symbol=symbol, period=period)
        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
            db = MarketSessionLocal()
            try:
                rows = db.execute(sa_text(
                    "SELECT exchange, COUNT(*), MIN(timestamp), MAX(timestamp) "
                    "FROM crypto_klines WHERE symbol=:sym AND period=:p "
                    "GROUP BY exchange ORDER BY COUNT(*) DESC"
                ), {"sym": symbol, "p": period}).fetchall()
                for r in rows:
                    years = (r[3] - r[2]) / (365.25 * 86400) if r[2] and r[3] else 0
                    report.by_exchange[r[0]] = {
                        "count": r[1], "first_ts": r[2],
                        "last_ts": r[3], "years": round(years, 1),
                    }
                    if r[1] > report.best_count:
                        report.best_count = r[1]
                        report.best_exchange = r[0]
                        report.best_years = years
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[DataCenter] coverage error: {e}")

        self._set_cache(cache_key, report)
        return report

    # ================================================================
    #  触发历史回填（走现有 KlineHistorySync，统一入口）
    # ================================================================

    def ensure_history(
        self,
        symbol: str,
        period: str = "1d",
        min_years: float = 2.0,
        exchange: str | None = None,
    ) -> bool:
        """
        确保某品种某周期有足够历史数据（P2 通道，不阻塞热路径）。
        不够则触发回填（走 KlineHistorySync）。

        exchange: 缺省用 active_exchange；trade 场景勿跨所。
        Returns: True = 数据已就绪。
        """
        symbol = symbol.upper().split("-")[0].split("/")[0]
        if not exchange:
            try:
                from backend.services.exchange_config import get_active_exchange
                exchange = get_active_exchange() or "asterdex"
            except Exception:
                exchange = "asterdex"
        exchange = (exchange or "asterdex").strip().lower()
        if exchange == "aster":
            exchange = "asterdex"

        cov = self.get_coverage(symbol, period)
        ex_info = (cov.by_exchange or {}).get(exchange) or {}
        years = float(ex_info.get("years") or 0)
        if years >= min_years:
            return True
        # 若该所无数据但其他所够，trade 场景仍要回填本所，不算就绪
        try:
            import asyncio

            from backend.services.kline_history_sync import KlineHistorySync

            async def _backfill():
                sync = KlineHistorySync()
                days = int(min_years * 365)
                await sync.start_sync(
                    symbols=[symbol], periods=[period], days=days, exchange=exchange,
                )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(_backfill())
                else:
                    loop.run_until_complete(_backfill())
            except RuntimeError:
                asyncio.run(_backfill())
            logger.info(
                f"[DataCenter] 触发回填 {symbol}/{period}@{exchange} ({min_years}y)"
            )
        except Exception as e:
            logger.warning(f"[DataCenter] 回填触发失败: {e}")
        return False

    def list_symbols(self, exchange: str | None = None, status: str = "trading") -> list[str]:
        """可交易目录（symbol_catalog）；空则尝试 scanner 刷新。"""
        if not exchange:
            try:
                from backend.services.exchange_config import get_active_exchange
                exchange = get_active_exchange() or "asterdex"
            except Exception:
                exchange = "asterdex"
        exchange = (exchange or "asterdex").strip().lower()
        if exchange == "aster":
            exchange = "asterdex"
        try:
            from backend.services.kline_sync_meta import (
                list_catalog_symbols,
                refresh_catalog_from_scanner,
            )
            symbols = list_catalog_symbols(exchange, status=status)
            if not symbols:
                symbols = refresh_catalog_from_scanner(exchange)
            return symbols or []
        except Exception as e:
            logger.debug(f"[DataCenter] list_symbols 失败: {e}")
            return []

    def get_sync_heartbeats(self, exchange: str | None = None) -> list[dict]:
        """采集心跳（P0/P1/P2）。"""
        try:
            from backend.services.kline_sync_meta import get_heartbeats
            return get_heartbeats(exchange)
        except Exception:
            return []

    def get_catalog_coverage(self) -> list[dict]:
        """四所目录与 K 线覆盖（阶段2 运维）。"""
        try:
            from backend.services.kline_sync_meta import get_catalog_coverage
            return get_catalog_coverage()
        except Exception:
            return []

    # ================================================================
    #  全市场实时品种扫描（自动选币用）
    # ================================================================

    def get_all_market_tickers(self, exchanges: list[str] | None = None) -> dict[str, dict]:
        """
        全市场实时品种扫描 — 数据中心唯一数据源（DB 聚合，禁止直连交易所）。

        [2026-08-04 修复] 原实现直连 binance ccxt + HyperliquidClient REST，绕过了
        数据中心（与本类"唯一业务读入口"定位矛盾），且代理抖动时直接失败。
        现改为从 crypto_klines 最新 1d 聚合 price/volume_24h，从 symbol_catalog
        取交易所品种清单，全部走本机 DB，单一数据源、可回放、可审计。

        Returns: {symbol: {price, volume_24h, change_24h, exchanges: [..]}}
        """
        cache_key = ("all_tickers_db",)
        cached = self._get_cache(cache_key, self._ttl_price * 10)  # 15s
        if cached is not None:
            return cached

        if not exchanges:
            exchanges = ["asterdex", "binance", "okx", "bybit", "hyperliquid"]

        from sqlalchemy import text as sa_text

        from backend.database.connection import MarketSessionLocal

        merged: dict[str, dict] = {}
        try:
            db = MarketSessionLocal()
            try:
                # 品种清单：以 catalog 为准（数据中心的正式品种目录）
                catalog_rows = db.execute(sa_text(
                    "SELECT exchange, symbol FROM symbol_catalog "
                    "WHERE status IN ('trading','delivering','settling','0','1') "
                    "OR status IS NULL OR status=''"
                )).fetchall()
                symbols_by_ex: dict[str, set[str]] = {}
                for ex, sym in catalog_rows:
                    base = (sym or "").upper().split("-")[0].split("/")[0]
                    if not base:
                        continue
                    ex_n = (ex or "").strip().lower()
                    if ex_n == "aster":
                        ex_n = "asterdex"
                    symbols_by_ex.setdefault(ex_n, set()).add(base)

                # 各所最新 1d K 线：price/volume_24h 直接来自数据中心落库数据
                for ex in exchanges:
                    ex_n = (ex or "").strip().lower()
                    if ex_n == "aster":
                        ex_n = "asterdex"
                    rows = db.execute(sa_text("""
                        SELECT k.symbol, k.close_price, k.volume, k.amount,
                               (k.close_price * k.volume) AS quote_volume
                        FROM crypto_klines k
                        JOIN (
                            SELECT symbol, MAX("timestamp") AS mx
                            FROM crypto_klines
                            WHERE exchange = :ex AND period = '1d'
                            GROUP BY symbol
                        ) t ON t.symbol = k.symbol AND k."timestamp" = t.mx
                        WHERE k.exchange = :ex AND k.period = '1d'
                              AND k.close_price > 0
                    """), {"ex": ex_n}).fetchall()
                    for sym, close, vol, amount, qv in rows:
                        base = (sym or "").upper().split("-")[0].split("/")[0]
                        if not base or close is None or float(close) <= 0:
                            continue
                        price = float(close)
                        volume_usd = float(qv) if qv is not None else (
                            float(vol or 0) * price if vol is not None else 0.0
                        )
                        # 有真实成交额才进池（杜绝无流动性假币）
                        if volume_usd <= 0:
                            continue
                        entry = merged.get(base)
                        if entry is None:
                            merged[base] = {
                                "price": price,
                                "volume_24h": volume_usd,
                                "change_24h": 0.0,
                                "exchanges": [ex_n],
                            }
                        else:
                            entry["volume_24h"] = entry.get("volume_24h", 0) + volume_usd
                            if price > 0:
                                entry["price"] = price
                            if ex_n not in entry.get("exchanges", []):
                                entry["exchanges"].append(ex_n)

                # 24h 涨跌幅：从 1d K 线相邻两根计算（close vs prev close）
                for base in list(merged.keys()):
                    try:
                        rows = db.execute(sa_text("""
                            SELECT close_price FROM crypto_klines
                            WHERE exchange = 'asterdex' AND symbol = :sym AND period = '1d'
                            ORDER BY "timestamp" DESC LIMIT 2
                        """), {"sym": base}).fetchall()
                        if len(rows) >= 2 and rows[1][0]:
                            prev = float(rows[1][0])
                            cur = float(rows[0][0])
                            if prev > 0:
                                merged[base]["change_24h"] = round((cur - prev) / prev * 100, 2)
                    except Exception:
                        pass
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[DataCenter] DB ticker 聚合失败: {e}")

        # 按流动性排序
        sorted_merged = dict(sorted(merged.items(), key=lambda x: -x[1].get("volume_24h", 0)))
        if sorted_merged:
            self._set_cache(cache_key, sorted_merged)
            logger.info(f"[DataCenter] 全市场扫描(DB): {len(sorted_merged)} 个品种")
        else:
            logger.warning("[DataCenter] 全市场扫描(DB) 无数据，请检查数据中心采集是否正常")
        return sorted_merged

    def get_top_liquid_symbols(self, n: int = 50, min_volume_usd: float = 5_000_000) -> list[str]:
        """
        获取流动性最高的 N 个品种（自动选币的候选池）。

        从全市场 ticker 数据按 24h 成交额排序，取 top N。
        """
        tickers = self.get_all_market_tickers()
        result = []
        for sym, data in tickers.items():
            if data.get("volume_24h", 0) >= min_volume_usd:
                result.append(sym)
            if len(result) >= n:
                break
        return result

    # ================================================================
    #  缓存管理
    # ================================================================

    def _get_cache(self, key: tuple, ttl: float) -> Any:
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and (time.time() - cached["ts"]) < ttl:
                return cached["data"]
        return None

    def _set_cache(self, key: tuple, data: Any) -> None:
        with self._cache_lock:
            self._cache[key] = {"ts": time.time(), "data": data}

    def invalidate(self, symbol: str | None = None) -> None:
        """清除缓存。"""
        with self._cache_lock:
            if symbol:
                self._cache = {k: v for k, v in self._cache.items() if symbol.upper() not in str(k)}
            else:
                self._cache.clear()

    def stats(self) -> dict:
        with self._cache_lock:
            return {"cache_entries": len(self._cache), "exchanges": ALL_EXCHANGES}

    @staticmethod
    def _to_ts(val) -> int:
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        else:
            dt = val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())


# 全局单例 — 所有数据读取的唯一入口
data_center = UnifiedMarketDataCenter()
