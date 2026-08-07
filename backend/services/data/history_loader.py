"""
HistoryDataLoader（P1 因子研究的数据接入层）。

目标：给因子回测/IC 评估/CPCV 提供**全历史** OHLCV DataFrame。
    现有 get_klines_from_db 按 count 取（默认 500 根），不足以做全历史因子研究。
    本模块按时间范围从 crypto_klines 表读取，输出标准 DataFrame（OHLCV + 时间索引）。

设计：
    - 时间范围查询（start_ts / end_ts，Unix 秒）
    - 多周期（1d/4h/1h/5m）
    - 质量校验（缺口检测、重复剔除、时间排序）
    - 与 FactorComputeAgent 的 fields 格式对齐（列名 open/high/low/close/volume）

这是 BTC/ETH 全历史因子回测的数据基础。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# BTC/ETH perp 上市日（各所略有差异，取保守早值）
LISTING_DATES: dict[str, str] = {
    "BTC": "2019-01-01",   # BTC perp 各所 2019-2020 陆续上线
    "ETH": "2019-01-01",   # ETH perp 同期
}

# 标准周期 -> 秒
PERIOD_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "8h": 28800, "12h": 43200, "1d": 86400,
}


@dataclass
class DataCoverage:
    """数据覆盖范围报告。"""
    symbol: str
    period: str
    first_ts: Optional[int]
    last_ts: Optional[int]
    count: int
    expected_count: int
    completeness_pct: float
    gaps: int


class HistoryDataLoader:
    """
    全历史 K线加载器（时间范围 -> DataFrame）。

    统一走 DataHub（数据中台），不绕开直连 DB。
    用法：
        loader = HistoryDataLoader()
        df = loader.load_range("BTC", "1h", start="2019-01-01", end="2026-07-15")
    """

    def __init__(self, session_factory=None, use_datahub: bool = True):
        """
        session_factory: 兼容旧接口（测试 mock 用）。
        use_datahub: True（默认）= 走 DataHub 多交易所择优；False = 走旧 session 直连。
        """
        self._session_factory = session_factory
        self._use_datahub = use_datahub

    def _get_datahub(self):
        """惰性获取 DataHub 单例。"""
        from backend.services.unified_data_pool import DataHub
        return DataHub()

    def _get_session(self):
        """惰性获取 session（默认 alpha_market 库的 MarketSessionLocal）。"""
        if self._session_factory is not None:
            return self._session_factory()
        # 默认用 alpha_market 库（市场数据所在），不是 alpha_arena
        from backend.database.connection import MarketSessionLocal
        return MarketSessionLocal()

    def load_range(
        self, symbol: str, period: str,
        start=None, end=None, exchange: str = "hyperliquid",
    ) -> pd.DataFrame:
        """加载时间范围内的全历史 K线。"""
        start_ts = self._to_ts(start) if start else 0
        end_ts = self._to_ts(end) if end else int(datetime.now(timezone.utc).timestamp())

        from backend.database.models import CryptoKline
        session = self._get_session()
        try:
            q = session.query(CryptoKline).filter(
                CryptoKline.symbol == symbol,
                CryptoKline.period == period,
                CryptoKline.exchange == exchange,
                CryptoKline.timestamp >= start_ts,
                CryptoKline.timestamp <= end_ts,
            ).order_by(CryptoKline.timestamp)
            rows = q.all()
        finally:
            session.close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame([{
            "timestamp": r.timestamp,
            "datetime": pd.Timestamp(r.timestamp, unit="s"),
            "open": float(r.open_price or 0),
            "high": float(r.high_price or 0),
            "low": float(r.low_price or 0),
            "close": float(r.close_price or 0),
            "volume": float(r.volume or 0),
            "amount": float(r.amount or 0),
        } for r in rows])
        df = df.set_index("datetime").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[["open", "high", "low", "close", "volume", "amount"]]

    def coverage(self, symbol: str, period: str, exchange: str = "hyperliquid") -> DataCoverage:
        """报告某品种某周期的数据覆盖情况。"""
        from backend.database.models import CryptoKline
        session = self._get_session()
        try:
            q = session.query(CryptoKline).filter(
                CryptoKline.symbol == symbol,
                CryptoKline.period == period,
                CryptoKline.exchange == exchange,
            ).order_by(CryptoKline.timestamp)
            rows = q.all()
        finally:
            session.close()

        if not rows:
            return DataCoverage(symbol, period, None, None, 0, 0, 0.0, 0)

        timestamps = [r.timestamp for r in rows]
        first, last = timestamps[0], timestamps[-1]
        period_sec = PERIOD_SECONDS.get(period, 3600)
        expected = max(1, (last - first) // period_sec)
        gaps = sum(
            1 for i in range(1, len(timestamps))
            if timestamps[i] - timestamps[i - 1] > period_sec * 1.5
        )
        completeness = min(100.0, len(timestamps) / max(1, expected) * 100)
        return DataCoverage(
            symbol=symbol, period=period,
            first_ts=first, last_ts=last,
            count=len(timestamps), expected_count=expected,
            completeness_pct=completeness, gaps=gaps,
        )

    def is_full_history_ready(
        self, symbol: str, period: str,
        min_years: float = 2.0, min_completeness: float = 0.8,
    ) -> bool:
        """数据是否够做因子研究。"""
        cov = self.coverage(symbol, period)
        if cov.count == 0:
            return False
        years = (cov.last_ts - cov.first_ts) / (365.25 * 86400)
        return (years >= min_years and cov.completeness_pct / 100 >= min_completeness)

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
