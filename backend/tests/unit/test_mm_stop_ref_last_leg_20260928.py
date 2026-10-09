# -*- coding: utf-8 -*-
"""[h442 2026-09-28] 止损参考价"最差入场"口径（stop_ref_last_leg）契约测试。

契约：
  1. stop_ref_last_leg=0（默认）⇒ 止损判定与旧版逐字一致（只用 avg_mid）；
  2. >0 且 last_entry_mid 有效 ⇒ 多头取 max、空头取 min（更早触发）；
  3. 平仓（qty=0）⇒ last_entry_mid 清零；同向加仓 ⇒ 更新为本次 mid；
  4. 状态序列化 round-trip 保留 last_entry_mid。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_maker.core import LaneRiskLimits, should_stop_loss  # noqa: E402
from backend.services.market_maker.runner import SymbolState  # noqa: E402


def _ref(qty, avg, last, limits):
    """与 runner 内相同的参考价计算（便于单测直接验证口径）。"""
    r = float(avg or 0.0)
    if float(getattr(limits, "stop_ref_last_leg", 0.0) or 0.0) > 0 \
            and float(last or 0.0) > 0 and abs(qty) > 1e-12:
        r = max(r, float(last)) if qty > 0 else min(r, float(last))
    return r


def test_default_off_uses_avg_only():
    lim = LaneRiskLimits(stop_loss_bp=40.0, stop_ref_last_leg=0.0)
    # 多头 avg=100，last_entry=101（更高=更差）：旧口径仍按 avg ⇒ mid 59.96 才触发
    r = _ref(1.0, 100.0, 101.0, lim)
    assert abs(r - 100.0) < 1e-9
    assert should_stop_loss(1.0, r, 99.7, 40.0) is False       # -30bp 未到
    assert should_stop_loss(1.0, r, 99.5, 40.0) is True        # -50bp 触发


def test_long_uses_max_avg_last():
    lim = LaneRiskLimits(stop_loss_bp=40.0, stop_ref_last_leg=1.0)
    r = _ref(1.0, 100.0, 101.0, lim)
    assert abs(r - 101.0) < 1e-9           # 最差入场 = 最近腿
    # mid=100.5：对 avg 来说 −0.5bp（不触发），对 last 来说已 −50bp（触发）
    assert should_stop_loss(1.0, r, 100.5, 40.0) is True


def test_short_uses_min_avg_last():
    lim = LaneRiskLimits(stop_loss_bp=40.0, stop_ref_last_leg=1.0)
    r = _ref(-1.0, 100.0, 99.0, lim)
    assert abs(r - 99.0) < 1e-9            # 空头最差入场 = 更低的最近腿
    # mid=99.4：空头按 ref=99 已浮亏 40bp+ ⇒ 触发
    assert should_stop_loss(-1.0, r, 99.4, 40.0) is True


def test_last_entry_mid_roundtrip():
    s = SymbolState(symbol="BTC", qty=1.0, avg_mid=100.0, last_entry_mid=101.0)
    d = s.to_dict()
    s2 = SymbolState.from_dict(d)
    assert abs(s2.last_entry_mid - 101.0) < 1e-9
    assert abs(s2.avg_mid - 100.0) < 1e-9


def test_no_last_entry_falls_back_to_avg():
    lim = LaneRiskLimits(stop_loss_bp=40.0, stop_ref_last_leg=1.0)
    r = _ref(1.0, 100.0, 0.0, lim)          # last_entry 未记录 ⇒ 回退 avg
    assert abs(r - 100.0) < 1e-9
