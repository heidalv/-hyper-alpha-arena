"""
K线历史数据同步服务 - 统一数据中心的核心

所有历史数据拉取都通过这个服务，
数据获取复用 hyperliquid_market_data 已有连接，
不创建新的交易所连接实例。
"""

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from backend.database.connection import MarketSessionLocal
from backend.database.models import CryptoKline
from backend.services.exchange_config import get_active_exchange
from backend.services.trading_pairs_config import get_user_trading_pairs


class SyncStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass
class SyncSubTask:
    symbol: str
    period: str
    start_time: datetime
    end_time: datetime
    status: str = "pending"
    progress: float = 0.0
    total_expected: int = 0
    collected: int = 0
    existing_in_db: int = 0
    error: str = ""


@dataclass
class SyncProgress:
    status: SyncStatus = SyncStatus.IDLE
    exchange: str = ""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    current_task: str = ""
    overall_progress: float = 0.0
    started_at: Optional[datetime] = None
    estimated_remaining_seconds: int = 0
    sub_tasks: List[SyncSubTask] = field(default_factory=list)
    error: str = ""
    total_records_synced: int = 0


PERIOD_PRIORITY = ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]
PERIOD_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "8h": 28800, "12h": 43200, "1d": 86400,
}

logger = logging.getLogger(__name__)


def _configured_symbols() -> List[str]:
    return get_user_trading_pairs()


class KlineHistorySync:

    def __init__(self):
        self.progress = SyncProgress()
        self._stop_flag = False
        self._lock = asyncio.Lock()

    def get_progress(self) -> Dict[str, Any]:
        p = self.progress
        return {
            "status": p.status.value,
            "exchange": p.exchange,
            "total_tasks": p.total_tasks,
            "completed_tasks": p.completed_tasks,
            "failed_tasks": p.failed_tasks,
            "current_task": p.current_task,
            "overall_progress": round(p.overall_progress, 1),
            "started_at": p.started_at.isoformat() if p.started_at else None,
            "estimated_remaining_seconds": p.estimated_remaining_seconds,
            "total_records_synced": p.total_records_synced,
            "error": p.error,
            "sub_tasks": [
                {
                    "symbol": st.symbol, "period": st.period,
                    "status": st.status, "progress": round(st.progress, 1),
                    "total_expected": st.total_expected, "collected": st.collected,
                    "existing_in_db": st.existing_in_db, "error": st.error,
                } for st in p.sub_tasks
            ],
        }

    async def start_sync(
        self,
        symbols: List[str] = None,
        periods: List[str] = None,
        days: int = 365,
        exchange: str = None,
    ) -> Dict[str, Any]:
        async with self._lock:
            if self.progress.status == SyncStatus.RUNNING:
                return {"error": "同步任务已在运行中，请等待完成或先停止"}

            self._stop_flag = False
            symbols = symbols or _configured_symbols()
            periods = periods or PERIOD_PRIORITY
            exchange_arg = exchange
            exchange = (exchange or get_active_exchange() or "asterdex").strip().lower()
            if exchange == "aster":
                exchange = "asterdex"

            # 显式指定 exchange（如 data_center.ensure_history）时禁止跨所备选
            force_single = exchange_arg is not None
            available_exchanges = [exchange]
            try:
                test_ok = await asyncio.to_thread(self._test_exchange_connection, exchange)
                if not test_ok and not force_single:
                    for fallback_ex in ["binance", "bybit", "okx", "hyperliquid"]:
                        if fallback_ex == exchange:
                            continue
                        try:
                            fb_ok = await asyncio.to_thread(self._test_exchange_connection, fallback_ex)
                            if fb_ok:
                                available_exchanges.append(fallback_ex)
                                logger.info(f"[HistorySync] 主所 {exchange} 不可用，备选 {fallback_ex} 可用")
                        except Exception:
                            pass
                elif not test_ok:
                    logger.warning(
                        f"[HistorySync] 指定所 {exchange} 连接失败，仍单所回填（禁止跨所）"
                    )
            except Exception as e:
                return {"error": f"连接测试失败: {str(e)[:100]}，请确认代理/VPN已开启"}

            # 对每个 symbol/period，选择数据最全的交易所
            def _best_exchange_for(symbol, period, start_time, end_time):
                """多交易所择优：选 DB 里已有数据最多的所。"""
                best_ex = exchange
                best_count = 0
                for ex in available_exchanges:
                    cnt = self._count_existing(ex, symbol, period, start_time, end_time)
                    if cnt > best_count:
                        best_count = cnt
                        best_ex = ex
                # 如果主所数据不足（<50%期望），且 binance 可用且未试过，用 binance
                if best_count < expected * 0.5 and "binance" not in available_exchanges:
                    try:
                        if asyncio.get_event_loop().run_in_executor(None, self._test_exchange_connection, "binance"):
                            available_exchanges.append("binance")
                    except Exception:
                        pass
                return best_ex

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=days)

            sub_tasks = []
            for period in periods:
                for symbol in symbols:
                    period_sec = PERIOD_SECONDS.get(period, 3600)
                    expected = int((end_time - start_time).total_seconds() / period_sec)
                    existing = self._count_existing(exchange, symbol, period, start_time, end_time)

                    st = SyncSubTask(
                        symbol=symbol, period=period,
                        start_time=start_time, end_time=end_time,
                        total_expected=expected, existing_in_db=existing,
                    )
                    if existing > 0 and existing >= expected * 0.95:
                        st.status = "skipped"
                        st.progress = 100.0
                        st.collected = existing
                    else:
                        st.status = "pending"
                    sub_tasks.append(st)

            pending_count = sum(1 for st in sub_tasks if st.status == "pending")
            self.progress = SyncProgress(
                status=SyncStatus.RUNNING,
                exchange=exchange,
                total_tasks=len(sub_tasks),
                completed_tasks=sum(1 for st in sub_tasks if st.status == "skipped"),
                started_at=datetime.now(timezone.utc),
                sub_tasks=sub_tasks,
            )

            logger.info(f"[HistorySync] 启动: {len(symbols)} symbols × {len(periods)} periods = "
                        f"{len(sub_tasks)} tasks ({pending_count} pending)")

        asyncio.create_task(self._run_sync())

        return {
            "message": "同步任务已启动",
            "exchange": exchange,
            "symbols": symbols,
            "periods": periods,
            "days": days,
            "total_tasks": len(sub_tasks),
            "pending_tasks": pending_count,
            "skipped_tasks": self.progress.completed_tasks,
        }

    async def stop_sync(self):
        if self.progress.status != SyncStatus.RUNNING:
            return {"message": "没有正在运行的同步任务"}
        self._stop_flag = True
        self.progress.status = SyncStatus.STOPPING
        return {"message": "正在停止同步..."}

    def _test_exchange_connection(self, exchange: str) -> bool:
        """????? API ??????????????????? HL??"""
        ex = (exchange or "").strip().lower()
        if ex == "aster":
            ex = "asterdex"
        try:
            if ex == "hyperliquid":
                from backend.services.hyperliquid_market_data import get_default_hyperliquid_client
                return get_default_hyperliquid_client() is not None
            # ccxt ?????????????????load_markets ???
            from backend.services.market_aggregation.aggregate_collector_base import (
                _create_ccxt_public,
            )
            return _create_ccxt_public(ex, timeout=5000) is not None
        except Exception as ex_err:
            logger.error(f"[HistorySync] ????????? ({exchange}): {ex_err}")
            return False

    def _count_existing(self, exchange, symbol, period, start_time, end_time):
        try:
            with MarketSessionLocal() as db:
                count = db.query(CryptoKline).filter(
                    CryptoKline.exchange == exchange,
                    CryptoKline.symbol == symbol.upper(),
                    CryptoKline.period == period,
                    CryptoKline.timestamp >= int(start_time.timestamp()),
                    CryptoKline.timestamp <= int(end_time.timestamp()),
                ).count()
                return count
        except Exception:
            return 0

    async def _run_sync(self):
        sync_start = time.time()
        try:
            # 根因3修复：数据中台增强默认开（队列化采集 + 批量写入），不再静默禁用。
            if os.getenv("MARKET_DATA_QUEUE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}:
                await self._run_sync_queue(sync_start)
                return

            for sub_task in self.progress.sub_tasks:
                if self._stop_flag:
                    break
                if sub_task.status in ("skipped", "completed"):
                    continue

                self.progress.current_task = f"{sub_task.symbol}/{sub_task.period}"
                sub_task.status = "running"

                try:
                    collected = await self._sync_one(sub_task)
                    sub_task.collected = collected
                    sub_task.status = "completed"
                    sub_task.progress = 100.0
                    self.progress.completed_tasks += 1
                    self.progress.total_records_synced += collected
                    logger.info(f"[HistorySync] ✅ {sub_task.symbol}/{sub_task.period}: "
                                f"+{collected} ({self.progress.completed_tasks}/{self.progress.total_tasks})")
                except Exception as e:
                    sub_task.status = "failed"
                    sub_task.error = str(e)[:200]
                    self.progress.failed_tasks += 1
                    logger.error(f"[HistorySync] ❌ {sub_task.symbol}/{sub_task.period}: {e}")

                done = self.progress.completed_tasks + self.progress.failed_tasks
                skipped = sum(1 for st in self.progress.sub_tasks if st.status == "skipped")
                self.progress.overall_progress = (done + skipped) / self.progress.total_tasks * 100 if self.progress.total_tasks > 0 else 0

                elapsed = time.time() - sync_start
                actual_done = done - skipped  # 真正执行的任务数
                if actual_done > 0:
                    remaining_work = sum(1 for st in self.progress.sub_tasks if st.status == "pending")
                    self.progress.estimated_remaining_seconds = int(remaining_work * elapsed / actual_done)

            if self._stop_flag:
                self.progress.status = SyncStatus.PAUSED
            elif self.progress.failed_tasks > 0 and self.progress.completed_tasks == 0:
                self.progress.status = SyncStatus.FAILED
            else:
                self.progress.status = SyncStatus.COMPLETED
            self.progress.current_task = ""
            logger.info(f"[HistorySync] 完成: {self.progress.total_records_synced} records, "
                        f"{(time.time() - sync_start)/60:.1f} min")

        except Exception as e:
            self.progress.status = SyncStatus.FAILED
            self.progress.error = str(e)
            logger.error(f"[HistorySync] 主循环异常: {e}", exc_info=True)

    async def _run_sync_queue(self, sync_start: float):
        """并发 worker 队列模式，默认关闭，用于 Phase 1 灰度验证。"""
        worker_count = max(1, int(os.getenv("MARKET_DATA_QUEUE_WORKERS", "3")))
        queue: asyncio.Queue[SyncSubTask] = asyncio.Queue()
        for sub_task in self.progress.sub_tasks:
            if sub_task.status not in ("skipped", "completed"):
                await queue.put(sub_task)

        async def worker(worker_id: int):
            while not self._stop_flag:
                try:
                    sub_task = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                self.progress.current_task = f"{sub_task.symbol}/{sub_task.period}"
                sub_task.status = "running"
                try:
                    collected = await self._sync_one(sub_task)
                    sub_task.collected = collected
                    sub_task.status = "completed"
                    sub_task.progress = 100.0
                    self.progress.completed_tasks += 1
                    self.progress.total_records_synced += collected
                    logger.info(
                        f"[HistorySync][W{worker_id}] ✅ {sub_task.symbol}/{sub_task.period}: "
                        f"+{collected} ({self.progress.completed_tasks}/{self.progress.total_tasks})"
                    )
                except Exception as e:
                    sub_task.status = "failed"
                    sub_task.error = str(e)[:200]
                    self.progress.failed_tasks += 1
                    logger.error(f"[HistorySync][W{worker_id}] ❌ {sub_task.symbol}/{sub_task.period}: {e}")
                finally:
                    done = self.progress.completed_tasks + self.progress.failed_tasks
                    skipped = sum(1 for st in self.progress.sub_tasks if st.status == "skipped")
                    self.progress.overall_progress = (
                        (done + skipped) / self.progress.total_tasks * 100
                        if self.progress.total_tasks > 0 else 0
                    )
                    elapsed = time.time() - sync_start
                    actual_done = max(done - skipped, 1)
                    remaining_work = queue.qsize()
                    self.progress.estimated_remaining_seconds = int(remaining_work * elapsed / actual_done)
                    queue.task_done()

        workers = [asyncio.create_task(worker(i + 1)) for i in range(worker_count)]
        await asyncio.gather(*workers)

        if self._stop_flag:
            self.progress.status = SyncStatus.PAUSED
        elif self.progress.failed_tasks > 0 and self.progress.completed_tasks == 0:
            self.progress.status = SyncStatus.FAILED
        else:
            self.progress.status = SyncStatus.COMPLETED
        self.progress.current_task = ""
        logger.info(
            f"[HistorySync] 队列模式完成: {self.progress.total_records_synced} records, "
            f"{(time.time() - sync_start) / 60:.1f} min, workers={worker_count}"
        )

    async def _sync_one(self, sub_task: SyncSubTask) -> int:
        """同步单个 symbol+period
        
        核心：复用 hyperliquid_market_data 的已有连接，
        不创建新的 ccxt 实例。
        """
        exchange = self.progress.exchange
        symbol = sub_task.symbol
        period = sub_task.period
        period_sec = PERIOD_SECONDS.get(period, 3600)

        # 根据周期决定每次拉取的时间跨度
        # Binance API 每次最多1500条，所以每次拉取 1500 * period_sec 秒的数据
        batch_seconds = 1500 * period_sec
        batch_td = timedelta(seconds=batch_seconds)

        total_collected = 0
        consecutive_failures = 0  # [2026-07-30] 连续失败计数
        current_start = sub_task.start_time
        total_duration = (sub_task.end_time - sub_task.start_time).total_seconds()

        while current_start < sub_task.end_time:
            if self._stop_flag:
                break

            current_end = min(current_start + batch_td, sub_task.end_time)
            batch_count = min(1500, int((current_end - current_start).total_seconds() / period_sec))
            if batch_count < 1:
                batch_count = 1

            try:
                klines = await asyncio.to_thread(
                    self._fetch_klines_from_exchange,
                    exchange, symbol, period, current_start, batch_count
                )

                if klines:
                    inserted = await asyncio.to_thread(
                        self._insert_klines_batch, exchange, symbol, period, klines
                    )
                    total_collected += inserted
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except Exception as e:
                consecutive_failures += 1
                logger.warning(f"[HistorySync] {symbol}/{period} batch failed at "
                               f"{current_start.strftime('%Y-%m-%d')}: {e}")
                # [2026-07-30] 连续失败 3 次放弃该 symbol/period（避免对不存在
                # 的 symbol 如 BTC-PERP 反复请求几百次浪费 CPU/网络）
                if consecutive_failures >= 3:
                    logger.info(f"[HistorySync] {symbol}/{period}: 连续失败 {consecutive_failures} 次，跳过")
                    break

            elapsed_duration = (current_end - sub_task.start_time).total_seconds()
            sub_task.progress = min(elapsed_duration / total_duration * 100, 99.0)
            sub_task.collected = total_collected

            current_start = current_end
            await asyncio.sleep(0.5)

        return total_collected

    def _fetch_klines_from_exchange(self, exchange, symbol, period, since_dt, count):
        """??????? K ??????????????? Hyperliquid???
        ? HL ????? asterdex ????? hyperliquid ? HL ??????
        ???????? ccxt ?????"""
        ex = (exchange or "").strip().lower()
        if ex == "aster":
            ex = "asterdex"

        if ex == "hyperliquid":
            since_ms = int(since_dt.timestamp() * 1000)
            from backend.services.hyperliquid_market_data import get_default_hyperliquid_client
            client = get_default_hyperliquid_client()
            hl_ex = client.exchange
            if hasattr(client, "normalize_symbol"):
                ccxt_symbol = client.normalize_symbol(symbol)
            else:
                ccxt_symbol = f"{symbol.upper()}/USDC:USDC"
            ohlcv = hl_ex.fetch_ohlcv(ccxt_symbol, period, since=since_ms, limit=count)
            if not ohlcv:
                return []
            return [
                {
                    "timestamp": int(c[0] / 1000),
                    "open": float(c[1]), "high": float(c[2]),
                    "low": float(c[3]), "close": float(c[4]),
                    "volume": float(c[5] or 0),
                }
                for c in ohlcv
            ]

        # ??????asterdex/binance/okx/...????? ccxt ???
        from datetime import timedelta as _td
        import asyncio as _asyncio
        from backend.services.kline_collectors import ExchangeDataSourceFactory
        collector = ExchangeDataSourceFactory.get_collector(ex)
        period_sec = PERIOD_SECONDS.get(period, 3600)
        end_dt = since_dt + _td(seconds=max(count * period_sec, period_sec))
        bars = _asyncio.run(collector.fetch_historical_klines(symbol, since_dt, end_dt, period))
        out = []
        for b in (bars or []):
            ts = getattr(b, "timestamp", 0)
            if not ts:
                continue
            out.append({
                "timestamp": int(ts),
                "open": float(getattr(b, "open_price", 0) or 0),
                "high": float(getattr(b, "high_price", 0) or 0),
                "low": float(getattr(b, "low_price", 0) or 0),
                "close": float(getattr(b, "close_price", 0) or 0),
                "volume": float(getattr(b, "volume", 0) or 0),
            })
        return out

    def _insert_klines_batch(self, exchange, symbol, period, klines):
        """批量写入K线到数据库"""
        if not klines:
            return 0

        if os.getenv("MARKET_DATA_BATCH_WRITE_ENABLED", "true").lower() in {"1", "true", "yes", "on"}:
            try:
                from backend.services.market_data_write_batcher import market_data_write_batcher
                return market_data_write_batcher.insert_klines(exchange, symbol, period, klines)
            except Exception as e:
                logger.warning(f"[HistorySync] batch writer failed, fallback to legacy insert: {e}")

        try:
            with MarketSessionLocal() as db:
                from sqlalchemy import text

                from backend.database.dialect import dialect
                insert_sql = text(dialect.insert_on_conflict_do_nothing(
                    "crypto_klines",
                    "exchange, symbol, market, timestamp, period, datetime_str, "
                    "open_price, high_price, low_price, close_price, volume, environment",
                    ":exchange, :symbol, 'CRYPTO', :timestamp, :period, :datetime_str, "
                    ":open_price, :high_price, :low_price, :close_price, :volume, 'mainnet'",
                    conflict_cols="exchange, symbol, market, period, timestamp, environment",
                ))

                for k in klines:
                    ts = k["timestamp"]
                    dt_str = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                    db.execute(insert_sql, {
                        'exchange': exchange,
                        'symbol': symbol.upper(),
                        'timestamp': ts,
                        'period': period,
                        'datetime_str': dt_str,
                        'open_price': k['open'],
                        'high_price': k['high'],
                        'low_price': k['low'],
                        'close_price': k['close'],
                        'volume': k['volume'],
                    })
                db.commit()
                return len(klines)
        except Exception as e:
            logger.error(f"[HistorySync] DB insert failed: {e}")
            return 0


    # =========================================================================
    # 单交易对快速同步（独立于批量同步，不占主进度）
    # =========================================================================

    async def quick_sync_symbol(
        self,
        symbol: str,
        periods: List[str] = None,
        days: int = 365,
    ) -> Dict[str, Any]:
        """快速同步单个交易对的历史数据，返回同步结果"""
        symbol = symbol.upper().strip()
        periods = periods or PERIOD_PRIORITY
        exchange = get_active_exchange()

        try:
            ok = await asyncio.to_thread(self._test_exchange_connection, exchange)
            if not ok:
                return {"success": False, "error": "无法连接交易所API，请检查代理"}
        except Exception as e:
            return {"success": False, "error": f"连接失败: {str(e)[:100]}"}

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        total_collected = 0
        results = []

        for period in periods:
            period_sec = PERIOD_SECONDS.get(period, 3600)
            expected = int((end_time - start_time).total_seconds() / period_sec)
            existing = self._count_existing(exchange, symbol, period, start_time, end_time)

            if existing >= expected * 0.95:
                results.append({"period": period, "status": "skipped", "existing": existing})
                continue

            sub = SyncSubTask(
                symbol=symbol, period=period,
                start_time=start_time, end_time=end_time,
                total_expected=expected, existing_in_db=existing,
            )
            # 复用 _sync_one 的逻辑但需要临时设置 exchange
            saved_exchange = self.progress.exchange
            self.progress.exchange = exchange
            try:
                collected = await self._sync_one(sub)
                total_collected += collected
                results.append({"period": period, "status": "completed", "collected": collected})
                logger.info(f"[QuickSync] {symbol}/{period}: +{collected}")
            except Exception as e:
                results.append({"period": period, "status": "failed", "error": str(e)[:100]})
                logger.error(f"[QuickSync] {symbol}/{period} failed: {e}")
            finally:
                self.progress.exchange = saved_exchange

        return {
            "success": True,
            "symbol": symbol,
            "exchange": exchange,
            "total_collected": total_collected,
            "periods": results,
        }

    def check_symbol_data(self, symbol: str, days: int = 365) -> Dict[str, Any]:
        """检查某交易对在数据中心的数据可用性"""
        symbol = symbol.upper().strip()
        exchange = get_active_exchange()
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)

        period_status = {}
        total_existing = 0
        total_expected = 0

        for period in PERIOD_PRIORITY:
            period_sec = PERIOD_SECONDS.get(period, 3600)
            expected = int((end_time - start_time).total_seconds() / period_sec)
            existing = self._count_existing(exchange, symbol, period, start_time, end_time)
            coverage = existing / expected if expected > 0 else 0
            total_existing += existing
            total_expected += expected
            period_status[period] = {
                "existing": existing,
                "expected": expected,
                "coverage": round(coverage * 100, 1),
                "sufficient": coverage >= 0.8,
            }

        overall_coverage = total_existing / total_expected if total_expected > 0 else 0
        has_data = total_existing > 0
        sufficient = overall_coverage >= 0.5

        missing_periods = [p for p, s in period_status.items() if not s["sufficient"]]

        return {
            "symbol": symbol,
            "exchange": exchange,
            "has_data": has_data,
            "sufficient": sufficient,
            "overall_coverage": round(overall_coverage * 100, 1),
            "total_records": total_existing,
            "periods": period_status,
            "missing_periods": missing_periods,
            "needs_sync": not sufficient,
        }


history_sync = KlineHistorySync()


# ======================================================================
#  M1 深度回填 Runner（设计文档 §1.1）
#  启动时 + 每 6 小时按 (period → days) 目标深度回填热币；
#  断点续传由 KlineHistorySync 内部按 max(timestamp) 实现。
# ======================================================================


def _depth_targets() -> Dict[str, int]:
    """读取 KLINE_P1_DEPTH_DAYS_<PERIOD> 环境变量。"""
    defaults = {
        "1m": 30, "3m": 30, "5m": 30, "15m": 60,
        "30m": 90, "1h": 210, "4h": 365, "1d": 730, "1w": 520,
    }
    out: Dict[str, int] = {}
    for period, default in defaults.items():
        env = os.getenv(f"KLINE_P1_DEPTH_DAYS_{period.upper()}", "")
        try:
            out[period] = max(1, int(env)) if env else default
        except (TypeError, ValueError):
            out[period] = default
    return out


def _depth_symbols(max_symbols: int = 40) -> List[str]:
    """深度回填币种：热币优先（交易宇宙 + 核心币），可扩展至全 catalog。

    KLINE_DEPTH_BACKFILL_SYMBOLS=all 时使用 asterdex 全 catalog（含全部山寨币），
    解决「山寨币只有近端 1.7 天、主流币周期缺失」的数据不全问题；
    默认保持热币优先，避免全量回填抢占配额。
    """
    symbols: List[str] = []
    mode = os.getenv("KLINE_DEPTH_BACKFILL_SYMBOLS", "").strip().lower()
    try:
        from backend.services.kline_realtime_collector import get_trade_universe_symbols
        symbols = list(get_trade_universe_symbols() or [])
    except Exception:
        pass

    if mode in ("all", "catalog", "full"):
        try:
            from backend.services.kline_sync_meta import refresh_catalog_from_scanner
            from backend.services.exchange_config import get_active_exchange
            active = (get_active_exchange() or "asterdex").strip().lower()
            if active == "aster":
                active = "asterdex"
            catalog = refresh_catalog_from_scanner(active) or []
            if not catalog:
                # [2026-08-04 修复] scanner 首启未就绪（load_markets 慢）时，
                # 回退 DB 中已落库的 symbol_catalog，避免首轮只回填热币。
                try:
                    from backend.database.connection import MarketSessionLocal
                    from sqlalchemy import text as _sa_text
                    with MarketSessionLocal() as db:
                        rows = db.execute(_sa_text(
                            "SELECT symbol FROM symbol_catalog WHERE exchange=:ex ORDER BY symbol"
                        ), {"ex": active}).fetchall()
                    catalog = [str(r[0]).upper() for r in rows]
                    if catalog:
                        logger.info("[DepthBackfill] scanner 未就绪，回退 symbol_catalog: %d symbols",
                                    len(catalog))
                except Exception as _e:
                    logger.debug("[DepthBackfill] catalog DB 回退失败: %s", _e)
            # 热币排前，其余 catalog 币排后；上限由 max_symbols 控制
            hot = {s.upper() for s in symbols}
            rest = [s.upper() for s in catalog if s.upper() not in hot]
            symbols = symbols + rest
            logger.info("[DepthBackfill] 全 catalog 模式: 热币 %d + 山寨 %d = %d",
                        len(hot), len(rest), len(symbols))
        except Exception as e:
            logger.warning("[DepthBackfill] catalog 扩展失败，退回热币: %s", e)

    for s in ["BTC", "ETH", "SOL"]:
        if s not in symbols:
            symbols.append(s)
    return symbols[:max_symbols]


class DepthBackfillRunner:
    """后台深度回填线程：启动即跑一轮，之后每 6 小时补差。"""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_run_ts = 0.0
        self._last_error = ""

    def start(self) -> bool:
        if os.getenv("KLINE_DEPTH_BACKFILL_ENABLED", "false").lower() not in (
            "1", "true", "yes", "on",
        ):
            logger.info("[DepthBackfill] KLINE_DEPTH_BACKFILL_ENABLED=false，跳过启动")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="kline-depth-backfill",
            daemon=True,
        )
        self._thread.start()
        logger.info("[DepthBackfill] 深度回填线程已启动（目标: %s）", _depth_targets())
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def status(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_run_ts": self._last_run_ts,
            "last_error": self._last_error,
            "targets": _depth_targets(),
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._run_once())
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning("[DepthBackfill] 回填异常: %s", exc)
            self._last_run_ts = time.time()
            self._stop.wait(6 * 3600)

    async def _run_once(self) -> None:
        # [2026-08-04 修复] 多所深度回填：
        # - asterdex（主动所）：全 catalog 全周期深回填（KLINE_DEPTH_BACKFILL_SYMBOLS=all）；
        # - 冷所（binance/okx/bybit/hyperliquid）：只回填热币 + 交易宇宙（冷所是备选源，
        #   无需覆盖全部山寨币），补齐「主流币周期缺失」问题，同时避免请求量过大。
        # [2026-08-04 修复2] 交错执行：asterdex 与冷所轮换回填周期，避免冷所
        # 排在 asterdex 全量回填之后迟迟不启动（实测 asterdex 1m 回填 519 币
        # 需 1-2 小时，冷所被阻塞）。
        mode = os.getenv("KLINE_DEPTH_BACKFILL_SYMBOLS", "").strip().lower()
        if mode in ("all", "catalog", "full"):
            try:
                cap = max(50, int(os.getenv("KLINE_DEPTH_BACKFILL_SYMBOL_LIMIT", "200")))
            except (TypeError, ValueError):
                cap = 200
            hot_symbols = _depth_symbols(max_symbols=cap)
        else:
            hot_symbols = _depth_symbols()
        cold_cap = max(20, int(os.getenv("KLINE_DEPTH_BACKFILL_COLD_LIMIT", "60")))
        cold_symbols = _depth_symbols(max_symbols=cold_cap)
        targets = _depth_targets()
        if not hot_symbols:
            logger.warning("[DepthBackfill] 无回填币种，跳过本轮")
            return

        exchange_jobs = [("asterdex", hot_symbols)]
        if os.getenv("KLINE_DEPTH_BACKFILL_COLD_ENABLED", "true").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            for ex in ("binance", "okx", "bybit", "hyperliquid"):
                exchange_jobs.append((ex, cold_symbols))

        # asterdex 周期顺序：1h/4h/1d/1w 优先（规则引擎依赖），1m 大头放最后
        ordered_periods = ["1h", "4h", "1d", "1w", "30m", "15m", "5m", "3m", "1m"]
        period_days = {p: targets.get(p, 30) for p in ordered_periods if p in targets}

        def _cold_days(period: str, days: int) -> int:
            """冷所短周期浅回填：请求量巨大（1m 30天≈29批/币），冷所是备选源，
            短周期给浅深度即可覆盖「主流币短周期缺失」，同时避免冷所限流桶排队过久。
            - 1m/3m/5m：15 天
            - 15m/30m：45 天
            - 1h/4h/1d/1w：与 asterdex 相同深度
            """
            if period in ("1m", "3m", "5m"):
                return min(days, 15)
            if period in ("15m", "30m"):
                return min(days, 45)
            return days

        for period, days in period_days.items():
            if self._stop.is_set():
                return
            # [2026-08-04 修复3] 并行回填：asterdex 与各冷所同时推进。
            # 各所有独立限速器（asterdex=双桶、冷所=按所独立桶），互不抢配额，
            # 并行可在同一窗口期覆盖多所，避免冷所排在 asterdex 之后迟迟不启动。
            period_jobs = []
            for exchange, symbols in exchange_jobs:
                if self._stop.is_set():
                    return
                is_cold = exchange != "asterdex"
                job_days = _cold_days(period, days) if is_cold else days
                if is_cold and period in ("1m", "3m", "5m") and job_days < 7:
                    logger.debug(
                        "[DepthBackfill] %s 跳过短周期 %s（冷所浅回填 %d 天）",
                        exchange, period, job_days,
                    )
                    continue
                period_jobs.append((exchange, symbols, job_days))

            async def _job(exchange: str, symbols: list, days: int):
                logger.info(
                    "[DepthBackfill] %s %s × %d 天，symbols=%d",
                    exchange, period, days, len(symbols),
                )
                try:
                    ok_n, fail_n = await self._backfill_exchange_period(
                        exchange, symbols, period, days,
                    )
                    try:
                        from backend.services.kline_sync_meta import record_heartbeat
                        record_heartbeat(
                            exchange, pool="p2_depth", period=period,
                            symbols_ok=ok_n, symbols_fail=fail_n,
                            meta={
                                "days": days,
                                "symbols": len(symbols),
                                "source": "ccxt_collector",
                            },
                        )
                    except Exception:
                        pass
                    logger.info(
                        "[DepthBackfill] %s/%s 完成: ok=%d fail=%d",
                        exchange, period, ok_n, fail_n,
                    )
                except Exception as exc:
                    logger.warning("[DepthBackfill] %s/%s 失败: %s", exchange, period, exc)

            await asyncio.gather(*[
                _job(ex, syms, d) for (ex, syms, d) in period_jobs
            ])

    async def _backfill_exchange_period(
        self,
        exchange: str,
        symbols: list,
        period: str,
        days: int,
    ) -> tuple:
        """按交易所回填单个周期：断点 = 本地已有 max(timestamp)。

        asterdex 走 asterdex ccxt 采集器（带 _AsterdexRateLimiter 全局限速）；
        冷所走对应所 ccxt 采集器（各所独立限流，不与 asterdex 共享）。
        """
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        from backend.database.connection import MarketSessionLocal
        from backend.services.kline_collectors import ExchangeDataSourceFactory
        from backend.services.kline_data_service import kline_service
        from sqlalchemy import text as _sa_text

        try:
            collector = ExchangeDataSourceFactory.get_collector(exchange)
        except Exception as exc:
            logger.warning("[DepthBackfill] %s 无采集器: %s", exchange, str(exc)[:120])
            return 0, len(symbols)
        end_dt = _dt.now(_tz.utc)
        start_dt = end_dt - _td(days=days)
        ok_n = 0
        fail_n = 0
        for sym in symbols:
            if self._stop.is_set():
                break
            try:
                with MarketSessionLocal() as mdb:
                    row = mdb.execute(_sa_text(
                        "SELECT min(timestamp), max(timestamp) FROM crypto_klines "
                        "WHERE exchange=:ex AND symbol=:s AND period=:p"
                    ), {"ex": exchange, "s": sym.upper(), "p": period}).first()
                total_bars = 0
                if row and row[0]:
                    earliest = _dt.fromtimestamp(int(row[0]), tz=_tz.utc)
                    latest = _dt.fromtimestamp(int(row[1]), tz=_tz.utc)
                    windows = [
                        (start_dt, min(earliest - _td(minutes=1), end_dt)),  # 前缀缺口
                        (latest + _td(minutes=1), end_dt),                    # 后缀缺口
                    ]
                else:
                    windows = [(start_dt, end_dt)]
                for _ws, _we in windows:
                    if _ws >= _we:
                        continue
                    bars = await collector.fetch_historical_klines(
                        sym.upper(), _ws, _we, period,
                    )
                    if bars:
                        await kline_service._insert_kline_data(bars)
                        total_bars += len(bars)
                if total_bars:
                    logger.info(
                        "[DepthBackfill] %s/%s@%s: +%d bars (prefix/suffix 补齐)",
                        sym.upper(), period, exchange, total_bars,
                    )
                ok_n += 1
            except Exception as exc:
                fail_n += 1
                logger.warning("[DepthBackfill] %s/%s@%s 失败: %s",
                               sym.upper(), period, exchange, str(exc)[:140])
        return ok_n, fail_n

    async def _backfill_asterdex_period(
        self,
        symbols: list,
        period: str,
        days: int,
    ) -> tuple:
        """[deprecated] 兼容旧调用：等价于 asterdex 的 _backfill_exchange_period。"""
        return await self._backfill_exchange_period("asterdex", symbols, period, days)

    async def _backfill_asterdex_period(
        self,
        symbols: list,
        period: str,
        days: int,
    ) -> tuple:
        """用 asterdex ccxt 采集器逐币回填：断点 = 本地已有 max(timestamp)。"""
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        from backend.database.connection import MarketSessionLocal
        from backend.services.kline_collectors import ExchangeDataSourceFactory
        from backend.services.kline_data_service import kline_service
        from sqlalchemy import text as _sa_text

        collector = ExchangeDataSourceFactory.get_collector("asterdex")
        end_dt = _dt.now(_tz.utc)
        start_dt = end_dt - _td(days=days)
        ok_n = 0
        fail_n = 0
        for sym in symbols:
            if self._stop.is_set():
                break
            try:
                with MarketSessionLocal() as mdb:
                    row = mdb.execute(_sa_text(
                        "SELECT min(timestamp), max(timestamp) FROM crypto_klines "
                        "WHERE exchange='asterdex' AND symbol=:s AND period=:p"
                    ), {"s": sym.upper(), "p": period}).first()
                total_bars = 0
                if row and row[0]:
                    earliest = _dt.fromtimestamp(int(row[0]), tz=_tz.utc)
                    latest = _dt.fromtimestamp(int(row[1]), tz=_tz.utc)
                    windows = [
                        (start_dt, min(earliest - _td(minutes=1), end_dt)),  # 前缀缺口
                        (latest + _td(minutes=1), end_dt),                    # 后缀缺口
                    ]
                else:
                    windows = [(start_dt, end_dt)]
                for _ws, _we in windows:
                    if _ws >= _we:
                        continue
                    bars = await collector.fetch_historical_klines(
                        sym.upper(), _ws, _we, period,
                    )
                    if bars:
                        await kline_service._insert_kline_data(bars)
                        total_bars += len(bars)
                if total_bars:
                    logger.info(
                        "[DepthBackfill] %s/%s: +%d bars (prefix/suffix 补齐)",
                        sym.upper(), period, total_bars,
                    )
                ok_n += 1
            except Exception as exc:
                fail_n += 1
                logger.warning("[DepthBackfill] %s/%s 失败: %s", sym.upper(), period, str(exc)[:140])
        return ok_n, fail_n


depth_backfill_runner = DepthBackfillRunner()
