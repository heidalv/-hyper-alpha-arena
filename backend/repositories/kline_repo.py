"""
K-line data repository module
Provides K-line data database operations
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text
from typing import List, Optional, Tuple
from backend.database.models import CryptoKline
from backend.database.connection import get_db
from backend.database.dialect import dialect
from backend.services.exchange_config import get_active_exchange
import time
import ccxt


class KlineRepository:
    def __init__(self, db: Session):
        self.db = db

    def save_kline_data(self, symbol: str, market: str, period: str, kline_data: List[dict], exchange: str = None, environment: str = "mainnet") -> dict:
        """
        Save K-line data to database (atomic upsert mode)

        原实现用「SELECT 查重 + add() + commit()」的 check-then-insert 模式，
        在多 worker 并发写同一根 K 线时会在 commit 撞 crypto_klines 的唯一约束
        抛 UniqueViolation。现改为单条 ON CONFLICT DO UPDATE 的原子 upsert，
        既消除竞态，又保留"存在则更新 OHLCV"的语义。

        Args:
            symbol: Trading symbol
            market: Market symbol
            period: Time period
            kline_data: K-line data list
            exchange: Exchange name (hyperliquid, binance, etc.)
            environment: Environment (testnet or mainnet)

        Returns:
            Save result dict, contains total processed count
        """
        if exchange is None:
            exchange = get_active_exchange()

        # 过滤无效记录
        rows = []
        for item in kline_data:
            timestamp = item.get('timestamp')
            if not timestamp:
                continue
            rows.append({
                'exchange': exchange,
                'symbol': symbol,
                'market': market,
                'period': period,
                'timestamp': timestamp,
                'datetime_str': item.get('datetime', '') or '',
                'environment': environment,
                'open_price': item.get('open'),
                'high_price': item.get('high'),
                'low_price': item.get('low'),
                'close_price': item.get('close'),
                'volume': item.get('volume'),
                'amount': item.get('amount'),
                'change': item.get('chg'),
                'percent': item.get('percent'),
            })

        if not rows:
            return {'inserted': 0, 'updated': 0, 'total': 0}

        # [2026-08-15 P0-5 修复] 统一走 kline_write.upsert_klines：
        # 与 kline_data_service 同一条写通道（后写者胜 + NaN/时间戳清洗），
        # 消除此前「仓库 DO UPDATE vs 服务 DO NOTHING」的语义冲突。
        from backend.services.kline_write import upsert_klines

        try:
            stats = upsert_klines(self.db, rows)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        total = stats.get("written", len(rows))
        # upsert 无法低成本区分 inserted/updated，统一计为 total；保持返回结构兼容旧调用方。
        return {'inserted': 0, 'updated': 0, 'total': total}

    def get_kline_data(self, symbol: str, market: str, period: str, limit: int = 100, exchange: str = None, environment: str = "mainnet") -> List[CryptoKline]:
        """
        Get K-line data

        Args:
            symbol: Trading symbol
            market: Market symbol
            period: Time period
            limit: Limit count
            exchange: Exchange name
            environment: Environment (testnet or mainnet)

        Returns:
            K-line data list
        """
        if exchange is None:
            exchange = get_active_exchange()
        return self.db.query(CryptoKline).filter(
            and_(
                CryptoKline.exchange == exchange,
                CryptoKline.symbol == symbol,
                CryptoKline.market == market,
                CryptoKline.period == period,
                CryptoKline.environment == environment
            )
        ).order_by(CryptoKline.timestamp.desc()).limit(limit).all()

    def delete_old_kline_data(self, symbol: str, market: str, period: str, keep_days: int = 30, exchange: str = None, environment: str = "mainnet"):
        """
        Delete old K-line data

        Args:
            symbol: Trading symbol
            market: Market symbol
            period: Time period
            keep_days: Days to keep
            exchange: Exchange name
            environment: Environment (testnet or mainnet)
        """
        if exchange is None:
            exchange = get_active_exchange()
        cutoff_timestamp = int(time.time() - keep_days * 24 * 3600)

        self.db.query(CryptoKline).filter(
            and_(
                CryptoKline.exchange == exchange,
                CryptoKline.symbol == symbol,
                CryptoKline.market == market,
                CryptoKline.period == period,
                CryptoKline.timestamp < cutoff_timestamp,
                CryptoKline.environment == environment
            )
        ).delete()

        self.db.commit()

    def get_missing_ranges(self, exchange: str, symbol: str, period: str, start_ts: int, end_ts: int, environment: str = "mainnet") -> List[Tuple[int, int]]:
        """
        Find missing time ranges in stored K-line data

        Args:
            exchange: Exchange name
            symbol: Trading symbol
            period: Time period (1m, 5m, 1h, etc.)
            start_ts: Start timestamp (Unix timestamp in seconds)
            end_ts: End timestamp (Unix timestamp in seconds)
            environment: Environment (testnet or mainnet)

        Returns:
            List of (start, end) timestamp tuples for missing ranges
        """
        # Convert period to seconds
        period_seconds = self._period_to_seconds(period)
        if not period_seconds:
            return [(start_ts, end_ts)]

        # Get existing timestamps in the range
        existing_data = self.db.query(CryptoKline.timestamp).filter(
            and_(
                CryptoKline.exchange == exchange,
                CryptoKline.symbol == symbol,
                CryptoKline.period == period,
                CryptoKline.timestamp >= start_ts,
                CryptoKline.timestamp <= end_ts,
                CryptoKline.environment == environment
            )
        ).order_by(CryptoKline.timestamp).all()

        if not existing_data:
            return [(start_ts, end_ts)]

        existing_timestamps = [row[0] for row in existing_data]
        missing_ranges = []

        # Check for gaps
        current_ts = start_ts
        for ts in existing_timestamps:
            if ts > current_ts:
                missing_ranges.append((current_ts, ts - period_seconds))
            current_ts = max(current_ts, ts + period_seconds)

        # Check final gap
        if current_ts <= end_ts:
            missing_ranges.append((current_ts, end_ts))

        return missing_ranges

    def ensure_history(self, exchange: str, symbol: str, period: str, start_ts: int, end_ts: int, environment: str = "mainnet") -> List[CryptoKline]:
        """
        Ensure K-line history is available for the given range, fetch missing data if needed

        Args:
            exchange: Exchange name
            symbol: Trading symbol
            period: Time period
            start_ts: Start timestamp (Unix timestamp in seconds)
            end_ts: End timestamp (Unix timestamp in seconds)
            environment: Environment (testnet or mainnet)

        Returns:
            Complete K-line data for the requested range
        """
        # Find missing ranges
        missing_ranges = self.get_missing_ranges(exchange, symbol, period, start_ts, end_ts, environment)

        # Fetch missing data for each range
        for range_start, range_end in missing_ranges:
            try:
                self._fetch_and_store_range(exchange, symbol, period, range_start, range_end, environment)
            except Exception as e:
                print(f"Failed to fetch data for {exchange}:{symbol} {period} [{range_start}-{range_end}] {environment}: {e}")

        # Return complete data
        return self.db.query(CryptoKline).filter(
            and_(
                CryptoKline.exchange == exchange,
                CryptoKline.symbol == symbol,
                CryptoKline.period == period,
                CryptoKline.timestamp >= start_ts,
                CryptoKline.timestamp <= end_ts,
                CryptoKline.environment == environment
            )
        ).order_by(CryptoKline.timestamp).all()

    def _period_to_seconds(self, period: str) -> Optional[int]:
        """Convert period string to seconds"""
        period_map = {
            '1m': 60,
            '3m': 180,
            '5m': 300,
            '15m': 900,
            '30m': 1800,
            '1h': 3600,
            '4h': 14400,
            '1d': 86400,
            '1w': 604800,
            '1M': 2592000,
        }
        return period_map.get(period)

    def _fetch_and_store_range(self, exchange: str, symbol: str, period: str, start_ts: int, end_ts: int, environment: str = "mainnet"):
        """补齐指定时间范围的 K 线历史：hyperliquid 走其客户端，asterdex/binance/okx 等走数据源工厂。"""
        import asyncio as _asyncio
        from datetime import datetime as _dt, timezone as _tz
        ex = (exchange or "").strip().lower()
        if ex == "aster":
            ex = "asterdex"

        if ex == "hyperliquid":
            # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止仓库层直连
            # HL K线补齐（历史补齐统一由数据中心的 DepthBackfill/P1 承担）。
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                return
            try:
                from services.hyperliquid_market_data import get_kline_data_from_hyperliquid
                period_seconds = self._period_to_seconds(period)
                limit = min(1000, (end_ts - start_ts) // period_seconds) if period_seconds else 200
                kline_data = get_kline_data_from_hyperliquid(symbol, period, limit, persist=False)
                if kline_data:
                    self.save_kline_data(symbol, "CRYPTO", period, kline_data, exchange)
            except Exception as e:
                print(f"Failed to fetch Hyperliquid data for {symbol}: {e}")
            return

        # asterdex/binance/okx 等其余所：经数据源工厂拉历史并写库
        try:
            from backend.services.kline_collectors import ExchangeDataSourceFactory
            from backend.services.kline_data_service import kline_service
            collector = ExchangeDataSourceFactory.get_collector(ex)
            start_dt = _dt.fromtimestamp(int(start_ts), tz=_tz.utc)
            end_dt = _dt.fromtimestamp(int(end_ts), tz=_tz.utc)

            async def _fetch():
                return await collector.fetch_historical_klines(symbol, start_dt, end_dt, period)

            bars = _asyncio.run(_fetch())
            if bars:
                kline_service._insert_kline_data(bars)
        except Exception as e:
            print(f"Failed to fetch K-line data for {symbol}@{ex}: {e}")
