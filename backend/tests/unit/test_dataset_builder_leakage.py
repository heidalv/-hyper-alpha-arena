"""
[2026-08-15 阶段3 T4] 训练数据装配器测试：防泄漏 / 覆盖率门 / K线写清洗。

覆盖：
1. 事件特征点-in-time（事件 ts ≤ bar 收盘才计入，之后的事件归下一根 bar）；
2. 事件时间戳 permutation 后事件特征分布改变（泄漏测试的机制校验）；
3. kline_write.sanitize_kline_row 的 NaN/非法时间戳拒绝；
4. data_availability_report 在无 DB 时的诚实降级。
"""
import pandas as pd
import pytest


class TestEventFeatureLeakage:
    def test_event_at_bar_close_counts_only_for_that_bar(self):
        from backend.services.factor_engine.dataset_builder import aggregate_event_features

        bar_ends = pd.DatetimeIndex(
            [pd.Timestamp("2026-08-15 12:00", tz="UTC"),
             pd.Timestamp("2026-08-15 13:00", tz="UTC")]
        )
        # 事件恰好在 12:00（bar0 收盘时刻）→ 计入 bar0；且因 24h 窗口延续，
        # 后续 bar1 也应计入（过去事件持续有效，这是窗口语义而非泄漏）。
        events = pd.DataFrame({
            "ts": pd.to_datetime([pd.Timestamp("2026-08-15 12:00", tz="UTC")]),
            "kind": ["news"],
            "score": [1.0],
        })
        counts, scores = aggregate_event_features(events, bar_ends, event_window_hours=24)
        assert counts == [1, 1], f"收盘时刻事件应计入当前及后续窗口内 bar，实际 {counts}"
        assert scores == [1.0, 1.0]

    def test_event_after_bar_close_never_leaks_backward(self):
        from backend.services.factor_engine.dataset_builder import aggregate_event_features

        bar_ends = pd.DatetimeIndex(
            [pd.Timestamp("2026-08-15 12:00", tz="UTC"),
             pd.Timestamp("2026-08-15 13:00", tz="UTC")]
        )
        # 事件 12:00:01（bar0 收盘之后 1 秒）→ 绝不能计入 bar0，只能计入 bar1
        events = pd.DataFrame({
            "ts": pd.to_datetime([pd.Timestamp("2026-08-15 12:00:01", tz="UTC")]),
            "kind": ["macro"],
            "score": [5.0],
        })
        counts, scores = aggregate_event_features(events, bar_ends, event_window_hours=24)
        assert counts == [0, 1], f"收盘后事件泄漏回上一根 bar，实际 {counts}"
        assert scores == [0.0, 5.0]

    def test_window_boundary_respects_hours(self):
        from backend.services.factor_engine.dataset_builder import aggregate_event_features

        bar_ends = pd.DatetimeIndex([pd.Timestamp("2026-08-15 12:00", tz="UTC")])
        # 30 小时前的事件超出 24h 窗口 → 不计入
        events = pd.DataFrame({
            "ts": pd.to_datetime([pd.Timestamp("2026-08-14 06:00", tz="UTC")]),
            "kind": ["news"],
            "score": [3.0],
        })
        counts, scores = aggregate_event_features(events, bar_ends, event_window_hours=24)
        assert counts == [0] and scores == [0.0]

    def test_permutation_changes_feature_distribution(self):
        """泄漏测试机制：打乱事件时间戳后，事件特征序列必须改变（事件
        与 K 线时间对齐一旦被破坏，因子预测力应归零——此测试验证对齐
        确实敏感于时间戳）。用稀疏事件保证区分度。"""
        from backend.services.factor_engine.dataset_builder import aggregate_event_features

        bar_ends = pd.DatetimeIndex(
            pd.date_range("2026-08-14 00:00", periods=48, freq="1h", tz="UTC")
        )
        # 稀疏事件：前 6 个小时各一个事件
        ts = pd.date_range("2026-08-13 00:00", periods=6, freq="1h", tz="UTC")
        events = pd.DataFrame({"ts": ts, "kind": ["news"] * 6, "score": [1.0] * 6})
        counts_orig, _ = aggregate_event_features(events, bar_ends, event_window_hours=24)

        # 打乱事件时间戳本身（事件集合不变，时间位置变化）：
        # 对齐被破坏后特征序列必须改变。
        import numpy as np
        rng = np.random.default_rng(42)
        new_ts = sorted(rng.choice(bar_ends, size=6, replace=False))
        shuffled = events.copy()
        shuffled["ts"] = new_ts
        counts_shuffled, _ = aggregate_event_features(shuffled, bar_ends, event_window_hours=24)
        assert counts_orig != counts_shuffled, "事件对齐应敏感于时间戳（泄漏测试前提）"


class TestKlineWriteSanitize:
    def test_rejects_nan_ohlc(self):
        from backend.services.kline_write import sanitize_kline_row

        row = {"exchange": "asterdex", "symbol": "BTC", "period": "1m",
               "timestamp": 1786700000, "open_price": float("nan"),
               "high_price": 1.0, "low_price": 1.0, "close_price": 1.0}
        assert sanitize_kline_row(row) is None

    def test_rejects_millisecond_timestamp(self):
        from backend.services.kline_write import sanitize_kline_row

        row = {"exchange": "asterdex", "symbol": "BTC", "period": "1m",
               "timestamp": 1786700000000,  # 毫秒
               "open_price": 1.0, "high_price": 1.1, "low_price": 0.9, "close_price": 1.05}
        assert sanitize_kline_row(row) is None

    def test_rejects_future_timestamp(self):
        import time
        from backend.services.kline_write import sanitize_kline_row

        row = {"exchange": "asterdex", "symbol": "BTC", "period": "1m",
               "timestamp": int(time.time()) + 10 * 86400,
               "open_price": 1.0, "high_price": 1.1, "low_price": 0.9, "close_price": 1.05}
        assert sanitize_kline_row(row) is None

    def test_accepts_valid_row_and_normalizes(self):
        from backend.services.kline_write import sanitize_kline_row

        row = {"exchange": "asterdex", "symbol": "btc", "period": "1m",
               "timestamp": 1786700000, "open_price": 1.0, "high_price": 1.1,
               "low_price": 0.9, "close_price": 1.05, "volume": None}
        out = sanitize_kline_row(row)
        assert out is not None
        assert out["symbol"] == "BTC"
        assert out["timestamp"] == 1786700000
        assert out["volume"] is None


class TestAvailabilityReport:
    def test_report_fails_honest_when_no_klines(self, monkeypatch):
        """无 K 线（如 DB 不可用）时诚实返回 available=False，不抛异常。"""
        from backend.services.factor_engine import dataset_builder as dbm

        def _empty(*a, **k):
            return pd.DataFrame()

        monkeypatch.setattr(dbm, "load_base_klines", _empty)
        rep = dbm.data_availability_report("BTC", "1h")
        assert rep["available"] is False
