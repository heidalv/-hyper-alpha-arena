# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化] `expected_records_for`：按**周期**估算应有 K 线根数。

## 修的缺陷（我在这轮 D-5/D-6 核查时发现）
`BackfillManager.process_task` 原写死 `expected_records = (end-start)/60`
（**一律按 1 分钟**）⇒ 非 1m 任务的 `total_records` 成倍虚报：

| 周期 | 虚报倍数 |
|---|---|
| 4h | **240×** |
| 1d | **1440×** |
| 1w | **10080×** |

该字段会写入 `kline_collection_tasks.total_records`，运维按"预计 N 条"判断进度会被误导。
（`progress` 是按**时间窗**计算的，不受影响 ⇒ 此前只有"预计条数"失真。）
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.kline_backfill_manager import expected_records_for  # noqa: E402

T0 = datetime(2026, 9, 1, 0, 0, 0)


@pytest.mark.parametrize("period,days,want", [
    ("1m", 1, 1440),
    ("5m", 1, 288),
    ("15m", 1, 96),
    ("1h", 1, 24),
    ("4h", 1, 6),
    ("1d", 400, 400),
    ("1w", 400, 57),      # 400 天 ≈ 57 周
])
def test_expected_records_is_period_aware(period, days, want):
    got = expected_records_for(T0, T0 + timedelta(days=days), period)
    assert got == want, f"{period} × {days}天：期望 {want}，实际 {got}"


def test_unknown_period_defaults_to_one_minute_without_crash():
    assert expected_records_for(T0, T0 + timedelta(minutes=10), "??") == 10
    assert expected_records_for(T0, T0 + timedelta(minutes=10), "") == 10


def test_never_returns_zero_and_handles_bad_range():
    assert expected_records_for(T0, T0, "1d") == 1, "零长度窗口至少 1（避免除零/无意义）"
    assert expected_records_for(T0 + timedelta(days=1), T0, "1d") == 1, "反序窗口不得抛"


def test_manager_uses_the_helper_not_hardcoded_60():
    """钉住调用点：不得再出现写死的 `/ 60` 估算。"""
    src = (ROOT / "backend" / "services" / "kline_backfill_manager.py").read_text(encoding="utf-8")
    assert "expected_records_for(" in src
    assert "total_seconds() / 60" not in src, "旧的写死 1 分钟估算仍在"
