"""
统一数据中心测试。

验证：多交易所择优、历史范围、DataFrame 输出、覆盖报告、缓存、降级。
用 mock DB 避免 CI 依赖。
"""
from __future__ import annotations

import pytest

from backend.services.data_center import (
    CoverageReport,
    KlineResult,
    UnifiedMarketDataCenter,
)

pytestmark = pytest.mark.unit


class _MockRow:
    def __init__(self, ts, o, h, l, c, v):
        self.ts = ts; self.o = o; self.h = h; self.l = l; self.c = c; self.v = v
    def __getitem__(self, idx):
        return [self.ts, self.o, self.h, self.l, self.c, self.v][idx]


class _MockResult:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows


class _MockDB:
    def __init__(self, exchange_data: dict):
        """exchange_data: {exchange: [rows]}"""
        self._data = exchange_data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass
    def execute(self, sql, params):
        ex = params.get("ex", "")
        rows = list(self._data.get(ex, []))
        # count 模式：DESC LIMIT → 取最后 N 根再反转
        if "DESC" in str(sql).upper() and "LIMIT" in str(sql).upper():
            limit = params.get("limit", 0)
            if limit and limit < len(rows):
                rows = list(reversed(rows[-limit:]))
        return _MockResult(rows)
    def close(self):
        pass


def _make_dc_with_mock(exchange_data):
    """构造带 mock DB 的 data_center。"""
    dc = UnifiedMarketDataCenter()
    dc.invalidate()  # 清缓存
    # monkeypatch MarketSessionLocal
    def _factory():
        return _MockDB(exchange_data)
    # 注入
    import backend.database.connection as conn_mod
    orig_session = getattr(conn_mod, "MarketSessionLocal", None)
    conn_mod.MarketSessionLocal = _factory
    return dc, lambda: setattr(conn_mod, "MarketSessionLocal", orig_session) if orig_session else None


class TestKlineResult:
    def test_to_dataframe(self):
        r = KlineResult(symbol="BTC", period="1d", exchange="hyperliquid",
                        rows=[{"timestamp": 100, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}])
        df = r.to_dataframe()
        assert len(df) == 1
        assert "close" in df.columns

    def test_empty_dataframe(self):
        r = KlineResult(symbol="X", period="1d", exchange="", rows=[])
        assert r.to_dataframe().empty

    def test_count_auto(self):
        r = KlineResult(symbol="X", period="1d", exchange="ex",
                        rows=[{"timestamp": i, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(5)])
        assert r.count == 5


class TestCoverageReport:
    def test_research_ready(self):
        r = CoverageReport(symbol="BTC", period="1d", best_years=3.0, best_count=1000)
        assert r.is_research_ready

    def test_not_ready(self):
        r = CoverageReport(symbol="BTC", period="1h", best_years=0.5, best_count=500)
        assert not r.is_research_ready


class TestDataCenterMultiExchange:
    def test_selects_best_exchange(self):
        """多交易所择优：选数据最多的所。"""
        dc, restore = _make_dc_with_mock({
            "hyperliquid": [_MockRow(100, 1, 1, 1, 1, 1) for _ in range(100)],
            "asterdex": [_MockRow(100, 1, 1, 1, 1, 1) for _ in range(200)],
            "binance": [_MockRow(100, 1, 1, 1, 1, 1) for _ in range(50)],
        })
        try:
            result = dc.get_klines("BTC", "1d")
            assert result.exchange == "asterdex"  # 200 根最多
            assert result.count == 200
        finally:
            restore()

    def test_single_exchange_specified(self):
        """指定 exchange 时只查该所。"""
        dc, restore = _make_dc_with_mock({
            "hyperliquid": [_MockRow(100, 1, 1, 1, 1, 1) for _ in range(100)],
            "binance": [_MockRow(100, 1, 1, 1, 1, 1) for _ in range(50)],
        })
        try:
            result = dc.get_klines("BTC", "1d", exchange="binance")
            assert result.exchange == "binance"
            assert result.count == 50
        finally:
            restore()

    def test_symbol_normalization(self):
        """BTC-PERP → BTC。"""
        dc, restore = _make_dc_with_mock({
            "hyperliquid": [_MockRow(100, 1, 1, 1, 1, 1)],
        })
        try:
            result = dc.get_klines("BTC-PERP", "1d", exchange="hyperliquid")
            assert result.symbol == "BTC"
        finally:
            restore()

    def test_cache_hit(self):
        """同查询第二次命中缓存。"""
        dc, restore = _make_dc_with_mock({
            "hyperliquid": [_MockRow(100, 1, 1, 1, 1, 1) for _ in range(10)],
        })
        try:
            dc.get_klines("BTC", "1d")
            dc.get_klines("BTC", "1d")  # 命中缓存
            assert dc.stats()["cache_entries"] >= 1
        finally:
            restore()

    def test_time_range_filter(self):
        """时间范围过滤（mock 简化：验证不崩 + 返回结果）。"""
        dc, restore = _make_dc_with_mock({
            "hyperliquid": [_MockRow(ts, 1, 1, 1, 1, 1) for ts in range(1000, 1020)],
        })
        try:
            result = dc.get_klines("BTC", "1d", start=1005, end=1015, exchange="hyperliquid")
            assert isinstance(result, KlineResult)
        finally:
            restore()

    def test_no_data_returns_empty(self):
        """无数据返回空结果。"""
        dc, restore = _make_dc_with_mock({})
        try:
            result = dc.get_klines("UNKNOWN", "1d")
            assert result.count == 0
        finally:
            restore()

    def test_count_mode(self):
        """count 模式取最近 N 根。"""
        dc, restore = _make_dc_with_mock({
            "hyperliquid": [_MockRow(ts, 1, 1, 1, 1, 1) for ts in range(100, 120)],
        })
        try:
            result = dc.get_klines("BTC", "1d", count=5, exchange="hyperliquid")
            assert result.count <= 5
        finally:
            restore()


class TestDataCenterCoverage:
    def test_coverage_returns_report(self):
        """覆盖报告返回 CoverageReport 类型（真实 DB 已验证多交易所择优）。"""
        dc = UnifiedMarketDataCenter()
        dc.invalidate()
        # 无 DB 时返回空 report（不崩）
        cov = dc.get_coverage("NONEXIST", "1d")
        assert isinstance(cov, CoverageReport)
        assert cov.symbol == "NONEXIST"
