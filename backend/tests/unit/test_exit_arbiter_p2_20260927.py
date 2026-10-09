# -*- coding: utf-8 -*-
"""[P2 大轮回 2026-09-27] 出场唯一裁决序契约测试（exit_arbiter）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.exit.exit_arbiter import (  # noqa: E402
    _state,
    allow_close,
    enabled,
    priority_of,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("EXIT_ARBITER_ENABLED", "true")
    monkeypatch.setenv("EXIT_ARBITER_WINDOW_S", "10")
    _state.clear()


def test_priority_order_matches_design_92():
    """§9.2 唯一裁决序：硬止损 > 失效 > 保本 > 分批止盈 > 时间止损。"""
    assert priority_of("stop_loss") > priority_of("thesis_invalidation")
    assert priority_of("thesis_invalidation") > priority_of("breakeven")
    assert priority_of("breakeven") > priority_of("staged_tp")
    assert priority_of("staged_tp") > priority_of("max_hold_timeout")
    assert priority_of("emergency_drawdown") > priority_of("trailing")


def test_lower_priority_yields_within_window():
    ok1, why1 = allow_close(101, "stop_loss", full_close=True, ts=1000.0)
    assert ok1
    ok2, why2 = allow_close(101, "staged_tp", full_close=True, ts=1005.0)
    assert not ok2, "窗口内低优先级全平必须让路"
    assert "lower_priority" in why2


def test_higher_priority_executes_after_lower():
    ok1, _ = allow_close(101, "trailing", full_close=True, ts=1000.0)
    assert ok1
    ok2, why2 = allow_close(101, "stop_loss", full_close=True, ts=1005.0)
    assert ok2, "硬止损必须压过窗口内更早的低优先级平仓"


def test_equal_priority_ok():
    ok1, _ = allow_close(101, "trend_broken", full_close=True, ts=1000.0)
    assert ok1
    ok2, _ = allow_close(101, "rule_exit", full_close=True, ts=1005.0)
    assert ok2


def test_window_expiry_clears():
    ok1, _ = allow_close(101, "stop_loss", full_close=True, ts=1000.0)
    assert ok1
    ok2, _ = allow_close(101, "staged_tp", full_close=True, ts=1000.0 + 11.0)
    assert ok2, "超过窗口后低优先级不再让路"


def test_partial_legs_do_not_block_each_other():
    """分批止盈腿不互斥：部分平仓意图只登记、不让路。"""
    ok1, _ = allow_close(101, "staged_tp", full_close=False, ts=1000.0)
    assert ok1
    ok2, why2 = allow_close(101, "trailing", full_close=False, ts=1002.0)
    assert ok2 and "partial_leg" in why2


def test_full_close_after_partial_still_arbitrates_by_priority():
    ok1, _ = allow_close(101, "staged_tp", full_close=False, ts=1000.0)
    assert ok1
    ok2, _ = allow_close(101, "staged_tp", full_close=True, ts=1002.0)
    assert ok2, "部分腿之后的同优先级全平放行（无更高优先级在册）"
    # 全平（p60）之后，窗口内更低优先级的 trailing（p55）让路 —— §9.2 裁决序
    ok3, why3 = allow_close(101, "trailing", full_close=True, ts=1004.0)
    assert not ok3 and "lower_priority" in why3
    # 更高优先级的硬止损仍放行
    ok4, _ = allow_close(101, "stop_loss", full_close=True, ts=1006.0)
    assert ok4


def test_no_position_id_passes_through():
    ok, why = allow_close(None, "staged_tp", full_close=True, ts=1000.0)
    assert ok and why == "no_position_id"


def test_disabled_switch_fail_open(monkeypatch):
    monkeypatch.setenv("EXIT_ARBITER_ENABLED", "false")
    ok, why = allow_close(101, "staged_tp", full_close=True, ts=1000.0)
    assert ok and why == "arbiter_off"
