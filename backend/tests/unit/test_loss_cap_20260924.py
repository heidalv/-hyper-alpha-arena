"""[2026-09-24 用户指令 · 长线动态止损] 绝对亏损硬闸（LONG_LOSS_CAP_PCT）单测。

依据（09-15 后样本 n=15 kline / 12 agg，双价源）：−8% 硬闸 Δ总 +89.50 / +69.04，
前半Δ +5.92 / +6.44、后半Δ +83.58 / +62.60（均非负），最差单笔 −14.15 → −9.55 / −7.61。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.paper_trading_engine import PaperTradingEngine  # noqa: E402

hit = PaperTradingEngine._loss_cap_hit


def test_long_below_cap_not_hit():
    assert hit("long", 100.0, 95.0, 8.0) is False


def test_long_at_cap_hit():
    assert hit("long", 100.0, 92.0, 8.0) is True
    assert hit("long", 100.0, 90.0, 8.0) is True


def test_buy_alias_supported():
    assert hit("buy", 100.0, 91.0, 8.0) is True


def test_short_symmetric():
    assert hit("short", 100.0, 109.0, 8.0) is True
    assert hit("sell", 100.0, 107.0, 8.0) is False


def test_cap_disabled_when_zero_or_negative():
    assert hit("long", 100.0, 50.0, 0) is False
    assert hit("long", 100.0, 50.0, -5) is False


def test_invalid_inputs_fail_open():
    assert hit("long", 0, 50.0, 8.0) is False
    assert hit("long", 100.0, 0, 8.0) is False
    assert hit("", 100.0, 50.0, 8.0) is False


# ── [2026-09-24 用户指令 · 动态止盈 tick 级] 分档止盈档位判定 ──
stage_hit = PaperTradingEngine._staged_tp_stage_hit


def test_stage1_at_8pct():
    r = stage_hit(100.0, 108.0, [])
    assert r is not None and r[0] == 1 and abs(r[1] - 0.5) < 1e-9


def test_stage2_after_stage1():
    r = stage_hit(100.0, 115.0, [1])
    assert r is not None and r[0] == 2 and abs(r[1] - 0.5) < 1e-9


def test_stage3_clears_rest():
    r = stage_hit(100.0, 126.0, [1, 2])
    assert r is not None and r[0] == 3 and abs(r[1] - 1.0) < 1e-9


def test_no_hit_below_threshold_or_all_done():
    assert stage_hit(100.0, 107.9, []) is None
    assert stage_hit(100.0, 130.0, [1, 2, 3]) is None


def test_invalid_inputs_none():
    assert stage_hit(0, 100.0, []) is None
    assert stage_hit(100.0, 0, []) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
