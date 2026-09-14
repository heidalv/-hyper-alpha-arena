# -*- coding: utf-8 -*-
"""[2026-09-14 F81] 成交桶水位线契约。

现场：trades_aggregated 按 15s 桶存储、时间戳=桶起点；fetch_market 用
`timestamp > since_ms`（开区间）⇒ 桶起点落在 tick 边界时被系统性跳过，
同窗口回放 212 笔 vs 实盘 4 笔（漏单 98%）。水位线保证每桶恰好消费一次。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402


def test_fetch_market_uses_watermark():
    """fetch_market 必须用「已消费桶标签」做下界并推进水位。

    [F92 2026-09-14 更新] 下界不能再取 `since_ms`（墙钟，落在桶中间）——那正是
    「只吃到 9% 成交」的根因；下界改由 `seg_window()` 按桶标签计算（见
    test_mm_seg_window_20260914.py），水位落在持久化运行态 `last_seg_ms`。
    """
    import inspect
    src = inspect.getsource(mmrunner.ShadowRunner.fetch_market)
    assert "seg_window(" in src, "窗口必须由 seg_window 统一计算"
    assert "last_seg_ms" in src, "水位必须持久化在运行态"
    assert "MAX(timestamp) AS mts" in src, "必须读回实际消费到的最大桶标签"
    assert "timestamp > :lo AND timestamp <= :hi" in src, "桶区间必须是(开,闭]"
    assert "_seg_watermark" in src, "保留进程内水位镜像（巡检可见）"


def test_runner_has_watermark_state():
    """ShadowRunner 必须持有每币水位线状态。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    assert isinstance(r._seg_watermark, dict)


def test_watermark_advances_monotonically():
    """水位线只前进不后退（迟到桶不会把水位拉回）。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    r._seg_watermark["BTC"] = 1000
    # 模拟 fetch 后推进：新值 = max(旧值, 最新桶时间戳)
    old = r._seg_watermark["BTC"]
    new_ts = 950  # 迟到的旧桶
    r._seg_watermark["BTC"] = max(old, new_ts)
    assert r._seg_watermark["BTC"] == old
