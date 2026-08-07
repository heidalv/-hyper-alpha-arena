"""持仓时限：复审点 vs AI 延长；短线禁 AI 复审/延长。"""

from types import SimpleNamespace

from backend.services.position_hold_time import (
    get_position_hold_status,
    resolve_initial_expected_hold_hours,
    resolve_max_hold_seconds,
    is_short_no_ai_hold_nature,
)


def _pos(**kwargs):
    defaults = dict(
        trade_nature="swing",
        timeframe_tier="mid",
        expected_hold_hours=8.0,
        opened_at="2026-06-08T08:00:00+00:00",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_short_no_ai_natures():
    assert is_short_no_ai_hold_nature("scalp")
    assert is_short_no_ai_hold_nature("intraday")
    assert not is_short_no_ai_hold_nature("swing")


def test_scalp_initial_capped_by_review():
    """开仓初始 ≤ runtime 复审点，避免 nature=8h 假延长。"""
    h = resolve_initial_expected_hold_hours("scalp", "short")
    assert h <= 3.0 + 1e-6
    assert h > 0


def test_scalp_max_capped_no_ai_extend_label():
    """scalp 即使 DB 写了 8h，有效上限仍锁在复审点；不标 AI已延长。"""
    pos = _pos(
        trade_nature="scalp",
        timeframe_tier="short",
        expected_hold_hours=8.0,
        opened_at="2026-07-31T10:00:00+00:00",
    )
    st = get_position_hold_status(pos)
    assert st["max_hold_hours"] <= st["review_hold_hours"] + 1e-6
    assert st["hold_ai_extended"] is False
    assert st["hold_ai_reviewable"] is False
    assert st["hold_near_timeout"] is False
    assert st["extendable_hours"] == 0.0


def test_mid_ai_extended_only_when_above_initial():
    pos = _pos(trade_nature="swing", timeframe_tier="mid", expected_hold_hours=16.0)
    st = get_position_hold_status(pos)
    initial = resolve_initial_expected_hold_hours("swing", "mid")
    if 16.0 > initial + 0.05:
        assert st["hold_ai_extended"] is True
    assert st["hold_ai_reviewable"] is True


def test_ai_extend_increases_max_cap_for_mid():
    pos = _pos(expected_hold_hours=16.0)
    assert resolve_max_hold_seconds(pos) == 16 * 3600
