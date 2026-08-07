"""
P2.5 共享 Cache 测试 + P2.6b 多交易所时间对齐测试。

P2.5 完成标准：状态只通过事件更新；replay 复现任意窗口；线程安全。
P2.6b 完成标准：跨所快照时间戳对齐；漂移超阈上报。
"""
from __future__ import annotations

import threading

import pytest

from backend.services.cache.single_source import SingleSourceCache
from backend.services.contracts.types import (
    DataQuality,
    DataQualityFlag,
    Direction,
    FactorVector,
    Horizon,
    Insight,
    Instrument,
    MarketSnapshot,
    RegimeLabel,
)
from backend.services.exchange.time_align import TimeAligner

pytestmark = pytest.mark.unit


def _inst(sym="BTC-PERP"):
    return Instrument(symbol=sym, venue="hyperliquid", kind="perp")


def _snap(ts, sym="BTC-PERP"):
    return MarketSnapshot(ts_ns=ts, instrument=_inst(sym), bid=50000, ask=50001,
                          mid=50000.5, last_trade=50000, last_trade_size=0.1)


# ==================== P2.5 共享 Cache ====================

class TestCacheBasics:
    def test_update_and_get(self):
        cache = SingleSourceCache()
        s = _snap(1000)
        cache.update_snapshot(s)
        assert cache.get_snapshot("BTC-PERP") is not None
        assert cache.get_snapshot("BTC-PERP").mid == 50000.5

    def test_event_journal(self):
        """状态变更进事件日志（事件溯源）。"""
        cache = SingleSourceCache()
        cache.update_snapshot(_snap(1000))
        cache.update_snapshot(_snap(2000))
        assert cache.journal_size() == 2

    def test_replay_window(self):
        """replay 复现时间窗内事件。"""
        cache = SingleSourceCache()
        cache.update_snapshot(_snap(1000))
        cache.update_snapshot(_snap(2000))
        cache.update_snapshot(_snap(3000))
        events = cache.replay(1500, 2500)
        assert len(events) == 1
        assert events[0].ts_ns == 2000

    def test_replay_by_type(self):
        cache = SingleSourceCache()
        cache.update_snapshot(_snap(1000))
        cache.update_regime(RegimeLabel(ts_ns=1100, regime="trend_high", confidence=0.8))
        snap_events = cache.replay_by_type("snapshot")
        regime_events = cache.replay_by_type("regime")
        assert len(snap_events) == 1
        assert len(regime_events) == 1


class TestCacheContracts:
    def test_factor_vector(self):
        cache = SingleSourceCache()
        fv = FactorVector(ts_ns=1000, instrument=_inst(), values={"mom": 0.5})
        cache.update_factor(fv)
        assert cache.get_factor("BTC-PERP").values["mom"] == 0.5

    def test_insight(self):
        cache = SingleSourceCache()
        ins = Insight(ts_ns=1000, instrument=_inst(), direction=Direction.LONG,
                      confidence=0.8, magnitude=0.02, period_ns=3600_000_000_000,
                      horizon=Horizon.SHORT, source="lgbm", expiry_ns=2e12)
        cache.update_insight(ins)
        assert cache.get_insight("BTC-PERP").direction == Direction.LONG

    def test_quality_flag(self):
        cache = SingleSourceCache()
        cache.update_quality(DataQualityFlag(
            ts_ns=1000, instrument=_inst(), quality=DataQuality.GAP, detail="seq gap",
        ))
        assert cache.get_quality("BTC-PERP").quality == DataQuality.GAP


class TestCacheThreadSafety:
    def test_concurrent_writes(self):
        """多线程并发写不损坏。"""
        cache = SingleSourceCache()
        def writer(sym):
            for i in range(100):
                cache.update_snapshot(_snap(i, sym=sym))
        threads = [threading.Thread(target=writer, args=(f"S{i}-PERP",)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        # 8 符号 × 100 = 800 事件
        assert cache.journal_size() == 800
        assert len(cache.all_symbols()) == 8


# ==================== P2.6b 多交易所时间对齐 ====================

class TestTimeAligner:
    def test_aligned_within_threshold(self):
        """时间戳接近 → OK。"""
        aligner = TimeAligner(max_drift_ms=100)
        aligner.update("binance",  1000.0)
        aligner.update("bybit",    1000.05)  # 50ms 漂移
        assert aligner.is_aligned()

    def test_drift_exceeds_threshold(self):
        """漂移超阈 → 不对齐。"""
        aligner = TimeAligner(max_drift_ms=100)
        aligner.update("binance", 1000.0)
        aligner.update("okx",     1000.2)    # 200ms 漂移
        assert not aligner.is_aligned()

    def test_watermark(self):
        """watermark = 最慢所的时间戳（保守对齐基线）。"""
        aligner = TimeAligner(max_drift_ms=1000)
        aligner.update("binance", 1000.0)
        aligner.update("bybit",   1001.0)
        aligner.update("okx",     1002.0)
        wm = aligner.watermark()
        assert wm == 1000.0  # 最慢

    def test_drift_flag(self):
        aligner = TimeAligner(max_drift_ms=50)
        aligner.update("binance", 1000.0)
        flag = aligner.update("okx", 1000.1)  # 100ms > 50ms
        assert flag is not None
        assert "okx" in flag.detail
