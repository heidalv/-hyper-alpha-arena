# -*- coding: utf-8 -*-
"""[续作 R10] P0 周期对齐开关单测（默认关 = 行为不变）。

背景（§66）：P0 每轮 106 币 × ['1m','3m','5m'] = 318 任务，而 K 线 IO 线程池只有
`KLINE_COLLECTOR_MAX_WORKERS=12` 个线程、单请求超时 6s ⇒ 队尾等待 ≈ 26s ⇒ 系统性超时
（中位成功率 95.0%、4.2% 轮次整轮全灭）。
3m/5m 的 bar 只在周期边界闭合，逐分钟重复抓取是冗余；开启对齐后任务量约减半而 1m 不变。
"""
from __future__ import annotations

from datetime import datetime

import pytest

import backend.services.kline_realtime_collector as K


class _Fake:
    """只借 `_periods_due_now` 这一个方法，避免起真实采集器。"""
    periods = ["1m", "3m", "5m", "15m", "1h"]

    _periods_due_now = K.KlineRealtimeCollector._periods_due_now


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KLINE_P0_PERIOD_ALIGN", raising=False)
    yield


def test_switch_default_off(monkeypatch):
    assert K._p0_period_align_enabled() is False
    monkeypatch.setenv("KLINE_P0_PERIOD_ALIGN", "true")
    assert K._p0_period_align_enabled() is True
    monkeypatch.setenv("KLINE_P0_PERIOD_ALIGN", "garbage")
    assert K._p0_period_align_enabled() is False


def test_default_behaviour_unchanged():
    """默认（关）必须逐字节等于旧行为：永远是 ['1m','3m','5m']。"""
    for minute in (0, 1, 3, 5, 59):
        assert _Fake()._periods_due_now(datetime(2026, 9, 24, 13, minute)) == ["1m", "3m", "5m"]


def test_aligned_keeps_1m_every_minute(monkeypatch):
    monkeypatch.setenv("KLINE_P0_PERIOD_ALIGN", "true")
    f = _Fake()
    assert f._periods_due_now(datetime(2026, 9, 24, 13, 1)) == ["1m"]
    assert f._periods_due_now(datetime(2026, 9, 24, 13, 2)) == ["1m"]


def test_aligned_fires_on_boundaries(monkeypatch):
    monkeypatch.setenv("KLINE_P0_PERIOD_ALIGN", "true")
    f = _Fake()
    assert f._periods_due_now(datetime(2026, 9, 24, 13, 3)) == ["1m", "3m"]
    assert f._periods_due_now(datetime(2026, 9, 24, 13, 5)) == ["1m", "5m"]
    assert f._periods_due_now(datetime(2026, 9, 24, 13, 15)) == ["1m", "3m", "5m"]
    assert f._periods_due_now(datetime(2026, 9, 24, 13, 0)) == ["1m", "3m", "5m"]


def test_task_volume_drops(monkeypatch):
    """对齐后 60 分钟内 3m/5m 被采集的次数应各为 20/12 次（而非 60）。"""
    monkeypatch.setenv("KLINE_P0_PERIOD_ALIGN", "true")
    f = _Fake()
    c3 = c5 = 0
    for m in range(60):
        ps = f._periods_due_now(datetime(2026, 9, 24, 13, m))
        c3 += "3m" in ps
        c5 += "5m" in ps
    assert (c3, c5) == (20, 12)
