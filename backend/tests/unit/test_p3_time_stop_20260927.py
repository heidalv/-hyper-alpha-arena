# -*- coding: utf-8 -*-
"""[P3 大轮回 2026-09-27] §6.1 时间止损契约（8h 减半 / 24h 全平）。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto.midlong_position_manager import (  # noqa: E402
    _time_stop_decision,
)


def test_under_8h_holds():
    assert _time_stop_decision(3.0, -0.01) == ("hold", "")
    assert _time_stop_decision(7.9, -0.05) == ("hold", "")


def test_8h_unprofitable_reduces():
    act, reason = _time_stop_decision(8.0, -0.01)
    assert act == "reduce" and reason == "time_stop_reduce"
    assert _time_stop_decision(12.0, 0.0)[0] == "reduce", "未达 μ（pnl=0）也算"


def test_8h_profitable_does_not_reduce():
    """已兑现（pnl>0）不按时间减半——盈利仓交给保本/trailing。"""
    assert _time_stop_decision(9.0, 0.001) == ("hold", "")


def test_24h_full_close_regardless_of_pnl():
    act, reason = _time_stop_decision(24.0, -0.02)
    assert act == "close" and reason == "time_stop_full"
    assert _time_stop_decision(30.0, 0.01)[0] == "close", "24h 后即使浮盈也全平"


def test_full_beats_reduce_at_boundary():
    assert _time_stop_decision(24.0, -0.01)[0] == "close"


def test_zero_threshold_disables_stage():
    assert _time_stop_decision(10.0, -0.01, reduce_h=0) == ("hold", "")
    assert _time_stop_decision(30.0, -0.01, full_h=0, reduce_h=8)[0] == "reduce"


def test_wiring_present_in_manage_position():
    """决策函数必须接在 manage_position 的规则维度链里（源码契约）。"""
    src = (ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"
           ).read_text(encoding="utf-8")
    assert "_time_stop_decision(" in src
    assert 'MIDLONG_TIME_STOP_ENABLED' in src
    assert 'reason="time_stop_full"' in src or 'reason=_ts_reason' in src
    assert 'reason="time_stop_reduce"' in src or 'reason=_ts_reason' in src
