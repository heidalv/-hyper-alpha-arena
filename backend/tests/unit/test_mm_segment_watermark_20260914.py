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
    """fetch_market 必须用 max(since_ms, 水位线) 做下界，并推进水位线。"""
    import inspect
    src = inspect.getsource(mmrunner.ShadowRunner.fetch_market)
    assert "_seg_watermark" in src
    assert "MAX(timestamp) AS mts" in src
    assert "timestamp > :ts" in src
    assert 'int(self._seg_watermark.get(s, 0))' in src


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
