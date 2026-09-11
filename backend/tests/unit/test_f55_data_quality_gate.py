# -*- coding: utf-8 -*-
"""[F55] 数据质量闸单测。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.data_quality_gate import (  # noqa: E402
    filter_clean_klines,
    gap_stats,
    is_plausible_kline,
    is_plausible_ts,
    normalize_epoch_ms,
    scan_kline_issues,
)


def _bar(ts, o, h, l, c):
    return {"timestamp": ts, "open_price": o, "high_price": h,
            "low_price": l, "close_price": c}


class TestTimestampNormalize:
    def test_seconds_and_millis_both_normalized(self):
        # 1788885000 秒 与 1788885000000 毫秒 是同一时刻
        assert normalize_epoch_ms(1788885000) == 1788885000000
        assert normalize_epoch_ms(1788885000000) == 1788885000000

    @pytest.mark.parametrize("bad", [None, 0, -1, "abc", ""])
    def test_invalid_returns_none(self, bad):
        assert normalize_epoch_ms(bad) is None

    def test_plausible_range(self):
        assert is_plausible_ts(normalize_epoch_ms(1788885000000))
        assert not is_plausible_ts(normalize_epoch_ms(58212 * 10**10))  # year 58212

    def test_real_corrupt_value_rejected(self):
        """DB 实测脏值：to_timestamp(1788457056901) 按秒解释 → year 58212。"""
        assert not is_plausible_ts(1788457056901 * 1000)


class TestKlineValidation:
    def test_normal_bar_ok(self):
        ok, why = is_plausible_kline(100, 101, 99, 100.5)
        assert ok and why == ""

    @pytest.mark.parametrize("args,expect", [
        ((100, 99, 101, 100), "high_lt_low"),      # 高低倒挂
        ((0, 1, 0.5, 0.8), "non_positive"),        # 非正价格
        ((100, 105, 95, 110), "close_out_of_range"),  # 收盘越界
    ])
    def test_invalid_bars(self, args, expect):
        ok, why = is_plausible_kline(*args)
        assert not ok and why == expect

    def test_gap_detected(self):
        ok, why = is_plausible_kline(200, 201, 199, 200, prev_close=100)
        assert not ok and why.startswith("gap_")

    def test_gap_threshold_configurable(self):
        ok, _ = is_plausible_kline(110, 111, 109, 110, prev_close=100, max_gap_pct=0.20)
        assert ok


class TestScanAndFilter:
    def test_scan_flags_corrupt_bar(self):
        rows = [
            _bar(1788885000000, 100, 101, 99, 100),
            _bar(1788885300000, 25000, 26000, 24000, 25500),   # 25000% 跳空
            _bar(1788885600000, 100, 101, 99, 100.5),
        ]
        issues = scan_kline_issues(rows)
        assert any(i.kind == "gap" for i in issues)
        clean = filter_clean_klines(rows)
        assert len(clean) == 2

    def test_gap_stats(self):
        rows = [_bar(i * 300_000 + 1788885000000, 100 + i, 101 + i, 99 + i, 100 + i)
                for i in range(50)]
        st = gap_stats(rows)
        assert st["n"] == 49 and st["p50"] < 100  # 平滑序列跳空很小

    def test_empty_input(self):
        assert scan_kline_issues([]) == []
        assert filter_clean_klines([]) == []
        assert gap_stats([])["n"] == 0
