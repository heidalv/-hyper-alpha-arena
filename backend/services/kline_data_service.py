"""
K线数据统一服务层 - 提供统一的数据操作接口
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from backend.database.connection import MarketSessionLocal
from backend.database.dialect import dialect
from backend.services.symbol_normalizer import normalize_symbol

from .exchange_config import get_active_exchange
from .kline_cache_service import kline_cache
from .kline_collector_executor import get_kline_collector_executor
from .kline_collectors import BaseKlineCollector, ExchangeDataSourceFactory, KlineData

logger = logging.getLogger(__name__)


# ── 多所成交量聚合（读侧，不改表/不动采集）──
# key=(symbol,period,count,exchanges,base_exchange) -> (ts, rows)
_KLINE_AGG_CACHE: Dict[tuple, tuple] = {}
_KLINE_AGG_LOCK = threading.Lock()
_KLINE_AGG_TTL_SEC = 60.0
_KLINE_AGG_MAX_ENTRIES = 500


def _kline_agg_exchanges() -> List[str]:
    raw = os.getenv(
        "KLINE_AGG_EXCHANGES", "asterdex,binance,okx,bybit,hyperliquid"
    )
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _kline_agg_enabled() -> bool:
    return os.getenv("KLINE_VOLUME_AGGREGATION_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


# 各周期允许的最大滞后（秒）。超过则视为"过期"，触发降级。
# 阈值比采集间隔宽松，避免边界抖动误判。
_PERIOD_STALE_SECONDS = {
    "1m": 180, "3m": 360, "5m": 600, "15m": 1500, "30m": 3000,
    "1h": 7200, "2h": 14400, "4h": 18000, "6h": 25200, "8h": 36000,
    "12h": 54000, "1d": 90000, "3d": 259200, "1w": 604800, "1M": 2592000,
}


def _klines_are_fresh(klines: Optional[List[Dict]], period: str) -> bool:
    """判断 K线列表是否新鲜（最新一根在允许滞后内）。

    用于自动降级判定：主交易所数据过期时读 hyperliquid 兜底。
    """
    if not klines:
        return False
    import time
    latest_ts = int(klines[-1].get("timestamp") or 0)
    if latest_ts <= 0:
        return False
    threshold = _PERIOD_STALE_SECONDS.get(period, 180)
    return (int(time.time()) - latest_ts) <= threshold


class KlineDataService:
    """K线数据统一服务 - 通过中央配置确定交易所"""

    def __init__(self):
        self.exchange_id: Optional[str] = None
        self.collector: Optional[BaseKlineCollector] = None
        self._initialized = False

    async def initialize(self):
        """初始化服务 - 从中央配置获取交易所"""
        if self._initialized:
            return

        try:
            self.exchange_id = get_active_exchange()
            self.collector = ExchangeDataSourceFactory.get_collector(self.exchange_id)
            self._initialized = True
            logger.info(f"[KlineDataService] ✅ 初始化完成: exchange={self.exchange_id}")

        except Exception as e:
            logger.error(f"[KlineDataService] 初始化失败: {e}，使用中央配置回退")
            self.exchange_id = get_active_exchange()
            self.collector = ExchangeDataSourceFactory.get_collector(self.exchange_id)
            self._initialized = True

    def _ensure_initialized(self):
        """确保服务已初始化"""
        if not self._initialized:
            raise RuntimeError("KlineDataService not initialized. Call initialize() first.")

    async def collect_current_kline(self, symbol: str, period: str = "1m") -> Optional[KlineData]:
        """采集当前分钟的K线数据，返回 KlineData 或 None"""
        self._ensure_initialized()

        try:
            # 使用已确定的采集器获取数据
            kline_data = await self.collector.fetch_current_kline(symbol, period)
            if not kline_data:
                logger.debug(f"No kline data for {symbol} on {self.exchange_id} (symbol may not be supported)")
                return None

            # 插入数据库（自动去重）
            success = await self._insert_kline_data([kline_data])
            return kline_data if success else None

        except Exception as e:
            logger.error(f"Failed to collect current kline for {symbol}: {e}")
            return None

    async def collect_historical_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        period: str = "1m"
    ) -> int:
        """采集历史K线数据，返回成功插入的记录数"""
        self._ensure_initialized()

        try:
            # 使用已确定的采集器获取历史数据
            klines_data = await self.collector.fetch_historical_klines(
                symbol, start_time, end_time, period
            )

            if not klines_data:
                logger.warning(f"No historical klines received for {symbol}")
                return 0

            # 批量插入数据库
            success = await self._insert_kline_data(klines_data)
            return len(klines_data) if success else 0

        except Exception as e:
            logger.error(f"Failed to collect historical klines for {symbol}: {e}")
            return 0

    async def _insert_kline_data(self, klines_data: List[KlineData], db_session: Session = None) -> bool:
        """批量插入K线数据到数据库（自动去重，分批提交提升性能）"""
        if not klines_data:
            return True

        # Run sync DB operations in K-line dedicated thread pool (not API default executor)
        return await asyncio.get_running_loop().run_in_executor(
            get_kline_collector_executor(), self._insert_kline_data_sync, klines_data, db_session
        )

    def _insert_kline_data_sync(self, klines_data: List[KlineData], db_session: Session = None) -> bool:
        """同步版：在线程池中执行，不阻塞事件循环。

        [2026-08-15 P0-5 修复] 改走统一写入口 kline_write.upsert_klines：
        - 语义统一为「后写者胜」（成形 bar 滚动校正 / kline_quality_repair
          收盘后校正都需要覆盖语义，与 kline_repo 一致）；
        - 写前清洗 NaN / 非法时间戳（毫秒/负数/未来值直接拒绝并计数告警）；
        - 写失败显式上抛，不再静默丢数据（原 DO NOTHING + except 吞错）。
        """
        db = db_session if db_session else MarketSessionLocal()
        should_close = not db_session

        try:
            from backend.services.kline_write import upsert_klines

            rows = []
            for kline in klines_data:
                # [2026-08-07 写入端归一化] 所有写入统一 normalize_symbol，
                # 杜绝 BTC / BTC-PERP / BTCUSDT 并存（symbol 全局唯一）。
                sym = normalize_symbol(kline.symbol) or str(kline.symbol or "").upper()
                rows.append({
                    'exchange': kline.exchange,
                    'symbol': sym,
                    'market': 'CRYPTO',
                    'timestamp': kline.timestamp,
                    'period': kline.period,
                    'open_price': kline.open_price,
                    'high_price': kline.high_price,
                    'low_price': kline.low_price,
                    'close_price': kline.close_price,
                    'volume': kline.volume,
                })

            stats = upsert_klines(db, rows)
            db.commit()
            logger.debug("Inserted klines via upsert_klines: %s", stats)
            return True

        except Exception as e:
            if should_close:
                db.rollback()
            logger.error(f"Failed to insert kline data: {e}")
            return False
        finally:
            if should_close:
                db.close()

    async def get_data_coverage(self, symbols: List[str] = None) -> List[Dict[str, Any]]:
        """获取数据覆盖情况"""
        self._ensure_initialized()

        try:
            with MarketSessionLocal() as db:
                query = """
                    SELECT * FROM kline_coverage_stats
                    WHERE exchange = :exchange
                """
                params = {'exchange': self.exchange_id}

                if symbols:
                    query += " AND symbol = ANY(:symbols)"
                    params['symbols'] = symbols

                query += " ORDER BY symbol, period"

                result = db.execute(text(query), params)
                return [dict(row._mapping) for row in result]

        except Exception as e:
            logger.error(f"Failed to get data coverage: {e}")
            return []

    async def detect_missing_ranges(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        period: str = "1m"
    ) -> List[tuple]:
        """检测缺失的数据时间段。

        [2026-08-15 R5 修复] 原实现期望序列硬编码 1 分钟步长，仅对 1m 正确；
        复用给其它周期会误判（例如把 5m/1h 正常间隔判为缺失）。现按周期取
        PERIOD_SECONDS 步长；未识别周期回退 60s（旧行为）。
        """
        self._ensure_initialized()

        # 周期 → 秒步长（与 data_center.PERIOD_SECONDS 对齐）
        _PERIOD_STEP = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800,
            "12h": 43200, "1d": 86400, "1w": 604800, "1M": 2592000,
        }
        step_sec = _PERIOD_STEP.get(str(period or "1m").strip().lower(), 60)

        try:
            with MarketSessionLocal() as db:
                # 获取现有的时间戳
                result = db.execute(text("""
                    SELECT timestamp FROM crypto_klines
                    WHERE exchange = :exchange AND symbol = :symbol
                    AND period = :period AND timestamp BETWEEN :start_ts AND :end_ts
                    ORDER BY timestamp
                """), {
                    'exchange': self.exchange_id,
                    'symbol': symbol,
                    'period': period,
                    'start_ts': int(start_time.timestamp()),
                    'end_ts': int(end_time.timestamp())
                })

                existing_timestamps = {row[0] for row in result}

                # 生成期望的时间戳序列（按周期步长）
                expected_timestamps = []
                current = int(start_time.timestamp())
                end_ts_i = int(end_time.timestamp())
                while current <= end_ts_i:
                    expected_timestamps.append(current)
                    current += step_sec

                # 找出缺失的时间段
                missing_ranges = []
                range_start = None

                for ts in expected_timestamps:
                    if ts not in existing_timestamps:
                        if range_start is None:
                            range_start = ts
                    else:
                        if range_start is not None:
                            missing_ranges.append((
                                datetime.utcfromtimestamp(range_start),
                                datetime.utcfromtimestamp(ts - step_sec)
                            ))
                            range_start = None

                # 处理最后一个缺失段
                if range_start is not None:
                    missing_ranges.append((
                        datetime.utcfromtimestamp(range_start),
                        end_time
                    ))

                return missing_ranges

        except Exception as e:
            logger.error(f"Failed to detect missing ranges: {e}")
            return []

    def get_supported_symbols(self) -> List[str]:
        """获取当前交易所支持的交易对"""
        self._ensure_initialized()
        return self.collector.get_supported_symbols()

    async def refresh_exchange_config(self):
        """刷新交易所配置（当用户切换交易所时调用）"""
        self._initialized = False
        await self.initialize()

    def get_klines_from_db(self, symbol: str, period: str, count: int = 500, exchange: str = None) -> List[Dict[str, Any]]:
        """从数据中心获取K线（决策用途，强制 active_exchange 同源）。

        2026-07-31：废除「主所过期 → 降级读 hyperliquid」。交易/决策不得跨所静默回退。
        """
        try:
            from backend.services.data_center import data_center
            result = data_center.get_klines(
                symbol, period, count=count, exchange=exchange, purpose="trade",
            )
            if result.rows:
                return result.rows[-count:] if len(result.rows) > count else result.rows
            return []
        except Exception as e:
            logger.debug(f"[KlineDataService] data_center 不可用，降级直查: {e}")

        if exchange is None:
            exchange = get_active_exchange()
        klines = self._query_klines_from_db(symbol, period, count, exchange)
        return klines[-count:] if klines and len(klines) > count else (klines or [])

    def count_klines_from_db(
        self,
        symbol_periods: List[tuple],
        exchange: str = None,
    ) -> Dict[str, int]:
        """批量统计 K 线行数（预检/健康检查等只看数量的场景）。

        一次 SQL 完成多 (symbol, period) 的 COUNT，避免逐对全量拉取再 len()。
        只统计 OHLCV 全非空的有效行，与 _query_klines_from_db 跳过空值行同口径。
        返回 {f"{symbol}:{period}": count}，查询失败或缺行的键回退 0。
        """
        from sqlalchemy import and_, func, tuple_

        from backend.database.models import CryptoKline

        pairs = []
        for sp in (symbol_periods or []):
            sym = str(sp[0]).strip().upper().split("-")[0].split("/")[0]
            tf = str(sp[1]).strip().lower()
            if sym and tf and (sym, tf) not in pairs:
                pairs.append((sym, tf))

        result: Dict[str, int] = {f"{s}:{p}": 0 for s, p in pairs}
        if not pairs:
            return result

        if exchange is None:
            exchange = get_active_exchange()

        try:
            with MarketSessionLocal() as db:
                rows = (
                    db.query(CryptoKline.symbol, CryptoKline.period, func.count())
                    .filter(
                        CryptoKline.exchange == exchange,
                        and_(
                            CryptoKline.open_price.isnot(None),
                            CryptoKline.high_price.isnot(None),
                            CryptoKline.low_price.isnot(None),
                            CryptoKline.close_price.isnot(None),
                            CryptoKline.volume.isnot(None),
                        ),
                        tuple_(CryptoKline.symbol, CryptoKline.period).in_(pairs),
                    )
                    .group_by(CryptoKline.symbol, CryptoKline.period)
                    .all()
                )
            for sym, tf, cnt in rows:
                result[f"{sym}:{tf}"] = int(cnt or 0)
        except Exception as e:
            logger.error(f"count_klines_from_db failed: {e}")
        return result

    def get_aggregated_klines(
        self,
        symbol: str,
        period: str,
        count: int = 500,
        exchanges: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """多所成交量聚合（价格保持 active_exchange 单所同源）。

        返回结构与 get_klines_from_db 完全一致：OHLC 取基准所，volume=各所同 bar
        成交量之和，并附加 volume_sources=参与聚合的所数。任何异常/无多所数据时
        回退基准所单源，绝不静默换成其它所的价格。

        开关：KLINE_VOLUME_AGGREGATION_ENABLED（默认 true）；
        聚合所列表：KLINE_AGG_EXCHANGES（默认 asterdex,binance,okx,bybit,hyperliquid）。
        """
        if not _kline_agg_enabled():
            return self.get_klines_from_db(symbol, period, count=count)

        # [2026-08-15 R4 修复] 本函数此前直查 DB + 缓存、无新鲜度拒绝，
        # 与 midlong_helpers 注释「决策热路径只走 data_center(purpose=trade)」
        # 矛盾——冻结币种的陈旧 K 线会继续喂给中长线指标。现按 data_center
        # is_fresh 同口径（stale ≤ period_sec*2+60）校验基准所最新 bar，
        # 过期返回空（调用方跳过该周期指标注入，fail-closed）。
        # 开关：KLINE_AGG_FRESHNESS_GATE_ENABLED（默认 true；单元测试可用 false 关闭）。
        _fresh_gate_on = os.getenv(
            "KLINE_AGG_FRESHNESS_GATE_ENABLED", "true"
        ).strip().lower() not in ("0", "false", "no", "off")
        from backend.services.data_center import PERIOD_SECONDS as _PS
        period_sec = float(_PS.get(period, 3600))
        fresh_window = period_sec * 2 + 60

        def _fresh(rows: List[Dict[str, Any]]) -> bool:
            if not _fresh_gate_on:
                return True
            if not rows:
                return False
            try:
                latest = max(int(r.get("timestamp") or 0) for r in rows)
                return (time.time() - latest) <= fresh_window
            except (TypeError, ValueError):
                return False

        base_ex = get_active_exchange()
        exs = [
            e for e in (exchanges or _kline_agg_exchanges())
            if e and str(e).strip().lower() != str(base_ex).strip().lower()
        ]
        cache_key = (str(symbol).upper(), period, int(count), tuple(exs), base_ex)
        now = time.time()
        with _KLINE_AGG_LOCK:
            hit = _KLINE_AGG_CACHE.get(cache_key)
            if hit is not None and now - float(hit[0]) >= _KLINE_AGG_TTL_SEC:
                _KLINE_AGG_CACHE.pop(cache_key, None)
                hit = None
        if hit and now - float(hit[0]) < _KLINE_AGG_TTL_SEC:
            if not _fresh(hit[1]):
                with _KLINE_AGG_LOCK:
                    _KLINE_AGG_CACHE.pop(cache_key, None)
                logger.warning("[KlineAgg] %s/%s 缓存已过期（%.0fs），拒绝返回", symbol, period, fresh_window)
                return []
            return [dict(r) for r in hit[1]]

        base_rows = self._query_klines_from_db(symbol, period, count, base_ex)
        if not base_rows:
            return []
        if not _fresh(base_rows):
            logger.warning(
                "[KlineAgg] %s/%s@%s 基准所 K 线过期（阈值 %.0fs），拒绝聚合返回",
                symbol, period, base_ex, fresh_window,
            )
            return []
        merged: Dict[int, Dict[str, Any]] = {}
        for row in base_rows:
            ts = int(row.get("timestamp") or 0)
            if ts <= 0:
                continue
            item = dict(row)
            item["volume"] = float(item.get("volume") or 0)
            item["volume_sources"] = 1
            merged[ts] = item
        for ex in exs:
            try:
                rows = self._query_klines_from_db(symbol, period, count, ex)
            except Exception as exc:
                logger.debug("[KlineAgg] %s/%s %s 查询跳过: %s", symbol, period, ex, exc)
                continue
            for row in rows:
                ts = int(row.get("timestamp") or 0)
                if ts in merged:
                    merged[ts]["volume"] += float(row.get("volume") or 0)
                    merged[ts]["volume_sources"] += 1
        out = [merged[k] for k in sorted(merged)]
        with _KLINE_AGG_LOCK:
            _KLINE_AGG_CACHE[cache_key] = (now, [dict(r) for r in out])
            # 有界缓存：按插入序淘汰最旧条目，避免 symbol×period 组合无限增长。
            while len(_KLINE_AGG_CACHE) > _KLINE_AGG_MAX_ENTRIES:
                _KLINE_AGG_CACHE.pop(next(iter(_KLINE_AGG_CACHE)), None)
        return out

    def _query_klines_from_db(self, symbol: str, period: str, count: int, exchange: str) -> List[Dict[str, Any]]:
        """单交易所 K线查询（含缓存），不做降级。"""
        # ── 缓存优先 ──
        cached = kline_cache.get_klines(symbol, period, exchange=exchange)
        if cached and len(cached) >= count:
            return cached[-count:]

        try:
            with MarketSessionLocal() as db:
                result = db.execute(text("""
                    SELECT timestamp, open_price, high_price, low_price, close_price, volume
                    FROM crypto_klines
                    WHERE exchange = :exchange AND symbol = :symbol AND period = :period
                    ORDER BY timestamp DESC
                    LIMIT :limit
                """), {
                    'exchange': exchange,
                    'symbol': symbol.upper(),
                    'period': period,
                    'limit': count
                })

                rows = result.fetchall()
                if not rows:
                    return []

                # 结果是倒序的，需要反转回来变成正序（时间从小到大）
                rows = list(reversed(rows))

                klines = []
                for row in rows:
                    ts = row[0]
                    # 跳过包含空值的行
                    if any(v is None for v in row):
                        logger.debug(f"Skipping kline row with null values: ts={ts}")
                        continue
                    klines.append({
                        'timestamp': ts,
                        'datetime': datetime.utcfromtimestamp(ts).isoformat(),
                        'open': float(row[1]),
                        'high': float(row[2]),
                        'low': float(row[3]),
                        'close': float(row[4]),
                        'volume': float(row[5])
                    })

                # 将查询结果写入缓存
                if klines:
                    kline_cache.set_klines(symbol, period, klines, exchange=exchange)

                return klines
        except Exception as e:
            logger.error(f"Failed to get klines from db ({exchange}): {e}")
            return []

    def query_klines(
        self,
        symbol: str,
        period: str = "1m",
        exchange: Optional[str] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
        order: str = "desc",
        purpose: str = "trade",
    ) -> List[Dict[str, Any]]:
        """M1 数据中心收口：统一 K 线查询门面。

        业务代码只允许通过本门面读 K 线：
        - 无时间范围：走 data_center.get_klines（多所择优/缓存）；
        - 带时间范围：存储层直查（本模块为白名单 #2）。
        返回统一 dict 格式：[{timestamp, datetime, open, high, low, close, volume}]。

        purpose:
            trade（默认）— 过期数据返回空（下单安全）
            research — 允许略过期数据（选币/因子/研究）
        """
        from sqlalchemy import asc as _asc
        from sqlalchemy import desc as _desc

        from backend.database.connection import MarketSessionLocal
        from backend.database.models import CryptoKline

        sym = str(symbol or "").upper().split("-")[0].split("/")[0]
        ex = (exchange or "").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        limit = max(1, min(int(limit or 500), 1000))
        purpose_l = (purpose or "trade").strip().lower()
        if purpose_l not in ("trade", "research"):
            purpose_l = "trade"

        if start_ts is None and end_ts is None:
            try:
                from backend.services.data_center import data_center
                result = data_center.get_klines(
                    sym, period, count=limit, exchange=ex or None,
                    purpose=purpose_l,
                )
                rows = result.rows[-limit:] if len(result.rows) > limit else result.rows
                if order == "asc":
                    return list(rows)
                return list(reversed(rows))
            except Exception:
                pass

        try:
            with MarketSessionLocal() as mdb:
                q = mdb.query(CryptoKline).filter(
                    CryptoKline.symbol == sym,
                    CryptoKline.period == period,
                )
                if ex:
                    q = q.filter(CryptoKline.exchange == ex)
                if start_ts is not None:
                    q = q.filter(CryptoKline.timestamp >= int(start_ts))
                if end_ts is not None:
                    q = q.filter(CryptoKline.timestamp <= int(end_ts))
                if order == "asc":
                    q = q.order_by(_asc(CryptoKline.timestamp))
                else:
                    q = q.order_by(_desc(CryptoKline.timestamp))
                q = q.limit(limit)
                rows = q.all()
            out = []
            for r in rows:
                out.append({
                    "timestamp": r.timestamp,
                    "datetime": str(getattr(r, "datetime_str", "") or ""),
                    "open": float(r.open_price or 0),
                    "high": float(r.high_price or 0),
                    "low": float(r.low_price or 0),
                    "close": float(r.close_price or 0),
                    "volume": float(r.volume or 0),
                })
            if order == "desc":
                out.reverse()
            return out
        except Exception:
            return []

    def get_klines_batch_from_db(
        self,
        symbols: List[str],
        period: str,
        count: int = 500,
        exchange: str = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """批量获取K线 — 经 data_center（trade 同源），禁止跨所降级。"""
        out: Dict[str, List[Dict[str, Any]]] = {}
        if not symbols:
            return out
        try:
            from backend.services.data_center import data_center
            batch = data_center.get_klines_batch(
                [s.upper() for s in symbols], period, count=count,
                exchange=exchange, purpose="trade",
            )
            for su, result in batch.items():
                rows = result.rows if result else []
                out[su] = rows[-count:] if len(rows) > count else rows
            return out
        except Exception as e:
            logger.warning(f"[KlineDataService] batch via data_center 失败，降级直查: {e}")

        if exchange is None:
            exchange = get_active_exchange()
        uncached = [s.upper() for s in symbols]
        rows_by_sym = self._query_klines_batch(uncached, period, count, exchange)
        for su in uncached:
            klines = rows_by_sym.get(su, [])
            if klines:
                kline_cache.set_klines(su, period, klines, exchange=exchange)
            out[su] = klines[-count:] if len(klines) > count else klines
        return out

    def _query_klines_batch(
        self,
        symbols: List[str],
        period: str,
        count: int,
        exchange: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """单交易所批量查询（一次 IN + 窗口函数取每标的最新 count 根），不做降级。"""
        if not symbols:
            return {}
        try:
            stmt = text("""
                SELECT symbol, timestamp, open_price, high_price, low_price, close_price, volume
                FROM (
                    SELECT symbol, timestamp, open_price, high_price, low_price,
                           close_price, volume,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol ORDER BY timestamp DESC
                           ) AS rn
                    FROM crypto_klines
                    WHERE exchange = :exchange AND period = :period
                      AND symbol IN :symbols
                ) t
                WHERE rn <= :limit
                ORDER BY symbol ASC, timestamp ASC
            """).bindparams(bindparam("symbols", expanding=True))

            with MarketSessionLocal() as db:
                result = db.execute(stmt, {
                    "exchange": exchange,
                    "period": period,
                    "symbols": [s.upper() for s in symbols],
                    "limit": count,
                })
                rows = result.fetchall()

            out: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                # row = (symbol, ts, open, high, low, close, volume)
                if any(v is None for v in row):
                    continue
                sym = row[0]
                ts = row[1]
                out.setdefault(sym, []).append({
                    "timestamp": ts,
                    "datetime": datetime.utcfromtimestamp(ts).isoformat(),
                    "open": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "close": float(row[5]),
                    "volume": float(row[6]),
                })
            return out
        except Exception as e:
            # 窗口函数不被支持或其它异常时，返回空让上层逐个兜底，绝不静默给错数据
            logger.warning(
                "[KlineDataService] 批量K线查询失败(%s,%s)，回退逐标的: %s",
                exchange, period, e,
            )
            return {}

    def get_period_health(self, symbol: str, period: str) -> Dict[str, Any]:
        """获取指定交易对和周期的数据健康状态"""
        exchange = get_active_exchange()
        try:
            with MarketSessionLocal() as db:
                # 统计基本信息
                result = db.execute(text("""
                    SELECT
                        COUNT(*) as record_count,
                        MIN(timestamp) as oldest_ts,
                        MAX(timestamp) as latest_ts
                    FROM crypto_klines
                    WHERE exchange = :exchange AND symbol = :symbol AND period = :period
                """), {
                    'exchange': exchange,
                    'symbol': symbol.upper(),
                    'period': period,
                })
                row = result.fetchone()
                record_count = row[0] or 0
                oldest_ts = row[1]
                latest_ts = row[2]

                if record_count == 0 or not oldest_ts or not latest_ts:
                    return {
                        'period': period,
                        'status': 'no_data',
                        'record_count': 0,
                        'coverage_pct': 0,
                        'freshness_seconds': None,
                        'gap_count': 0,
                        'latest_timestamp': None,
                        'oldest_timestamp': None,
                    }

                # 计算新鲜度
                now_ts = int(datetime.now(timezone.utc).timestamp())
                freshness = now_ts - int(latest_ts)

                # 计算覆盖率（基于时间段）
                period_seconds = {
                    '1m': 60, '3m': 180, '5m': 300, '15m': 900,
                    '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400
                }
                interval = period_seconds.get(period, 60)
                expected = max(1, (int(latest_ts) - int(oldest_ts)) // interval + 1)
                coverage = min(100, (record_count / expected) * 100) if expected > 0 else 0

                # 检测缺口（仅对 1m 做详细检测）
                gap_count = 0
                if period == '1m' and record_count > 0:
                    gap_result = db.execute(text("""
                        SELECT timestamp FROM crypto_klines
                        WHERE exchange = :exchange AND symbol = :symbol AND period = '1m'
                        AND timestamp >= :start_ts AND timestamp <= :end_ts
                        ORDER BY timestamp
                    """), {
                        'exchange': exchange,
                        'symbol': symbol.upper(),
                        'start_ts': int(latest_ts) - 86400,  # 最近24h
                        'end_ts': int(latest_ts),
                    })
                    timestamps = [r[0] for r in gap_result]
                    for i in range(1, len(timestamps)):
                        diff = timestamps[i] - timestamps[i - 1]
                        if diff > 120:  # 超过2分钟缺口
                            gap_count += 1

                # 判断状态
                status = 'healthy'
                if record_count == 0:
                    status = 'no_data'
                elif freshness > 3600:  # > 1小时
                    status = 'stale'
                elif coverage < 80:
                    status = 'degraded'
                elif gap_count > 5:
                    status = 'gaps'

                return {
                    'period': period,
                    'status': status,
                    'record_count': record_count,
                    'coverage_pct': round(coverage, 1),
                    'freshness_seconds': freshness,
                    'gap_count': gap_count,
                    'latest_timestamp': int(latest_ts),
                    'oldest_timestamp': int(oldest_ts),
                }

        except Exception as e:
            logger.error(f"Failed to get period health for {symbol}/{period}: {e}")
            return {
                'period': period,
                'status': 'error',
                'record_count': 0,
                'coverage_pct': 0,
                'freshness_seconds': None,
                'gap_count': 0,
                'latest_timestamp': None,
                'oldest_timestamp': None,
            }

    def get_multi_period_health(self, symbol: str) -> Dict[str, Any]:
        """获取所有周期的数据健康状态"""
        periods = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
        period_health = {}
        overall_status = 'healthy'

        for period in periods:
            health = self.get_period_health(symbol, period)
            period_health[period] = health
            if health['status'] in ('no_data', 'stale'):
                overall_status = 'poor'
            elif health['status'] in ('degraded', 'gaps') and overall_status == 'healthy':
                overall_status = 'degraded'

        return {
            'symbol': symbol.upper(),
            'overall_status': overall_status,
            'periods': period_health,
        }


# 全局服务实例
kline_service = KlineDataService()
