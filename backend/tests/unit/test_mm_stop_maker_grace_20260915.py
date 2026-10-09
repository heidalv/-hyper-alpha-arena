# -*- coding: utf-8 -*-
"""[F235 2026-09-15] 止损"先 maker 后 taker"宽限契约：

止损条件触发后，若 `stop_maker_grace_sec > 0`：
  · 第 1 个 tick 只记 `state.stop_since`，不 taker（减仓侧 maker 单继续挂，见正常报价流程）；
  · 宽限内价格回摆 ⇒ 计时归零（重新触发时宽限重算）；
  · 宽限到期仍未离场 ⇒ taker 平仓并归零计时。
  `stop_maker_grace_sec=0` = 触发即 taker（旧行为逐字一致 ✓）。

依据 F234 结构事实：平仓腿 -17~-30bp 是唯一亏损源；趋势行情里 maker 减仓单
等不到对手，但均值回归段可以 ⇒ 给一个可回放可调的宽限窗口 ✓。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402


def _make_state(qty: float = 1.0, avg_mid: float = 100.0):
    st = SymbolState(symbol="BTC", qty=qty, avg_px=avg_mid, avg_mid=avg_mid,
                     opened_ts=time.time() - 10.0)
    return st


def _limits(stop_bp: float = 10.0, grace: float = 0.0):
    return LaneRiskLimits(stop_loss_bp=stop_bp, stop_loss_vol_min=0.0,
                          stop_maker_grace_sec=grace, max_one_side_seconds=3600.0,
                          vol_pause_sigma=0.0, trend_pause_bp=0.0,
                          min_hold_seconds=0.0)  # F258 后默认 30s，本测试持仓仅 10s


def _tick(st, mid: float, now_ts: float, limits):
    dec, _ = plan_tick(
        state=st, mid=mid, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now_ts,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits, equity=300.0, fill_notional=30.0,
        taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=0.05,
        sigma_norm=1.5, book=None, marks={"BTC": 100.0},
        ofi=0.0, day_pnl_usd=0.0,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)
    return st, dec


def test_grace_zero_is_instant_taker():
    """grace=0 ⇒ 触发即 taker（旧行为）。"""
    t0 = time.time()
    st = _make_state()
    st, dec = _tick(st, mid=99.85, now_ts=t0, limits=_limits(grace=0.0))
    assert any(f.is_flatten for f in dec.fills), "grace=0 必须立即平仓"
    assert dec.skip == "stop_loss" and abs(st.qty) < 1e-9


def test_grace_first_tick_holds_and_sets_timer():
    """宽限第 1 tick：不 taker，记 stop_since。"""
    t0 = time.time()
    st = _make_state()
    st, dec = _tick(st, mid=99.85, now_ts=t0, limits=_limits(grace=60.0))
    assert not any(f.is_flatten for f in dec.fills), "宽限内第 1 tick 不得 taker"
    assert abs(st.qty - 1.0) < 1e-9, "仓位保持"
    assert abs(float(st.stop_since or 0.0) - t0) < 1e-6, "必须记下触发时刻"


def test_grace_expires_then_taker():
    """宽限到期仍浮亏 ⇒ taker 平仓，计时归零。"""
    t0 = time.time()
    st = _make_state()
    st, dec = _tick(st, mid=99.85, now_ts=t0, limits=_limits(grace=60.0))
    assert not dec.fills
    st, dec = _tick(st, mid=99.85, now_ts=t0 + 61.0, limits=_limits(grace=60.0))
    assert any(f.is_flatten for f in dec.fills), "宽限到期必须 taker 平仓"
    assert dec.skip == "stop_loss" and abs(st.qty) < 1e-9
    assert st.stop_since == 0.0, "平仓后计时必须归零"


def test_grace_resets_when_price_recovers():
    """宽限内价格回摆 ⇒ 计时归零；再触发时宽限重算（不得用旧时刻立即平仓）。"""
    t0 = time.time()
    st = _make_state()
    st, dec = _tick(st, mid=99.85, now_ts=t0, limits=_limits(grace=60.0))
    assert not dec.fills and st.stop_since > 0
    # 回摆：浮亏消失
    st, dec = _tick(st, mid=100.3, now_ts=t0 + 10.0, limits=_limits(grace=60.0))
    assert st.stop_since == 0.0, "止损条件解除必须清零计时"
    # 再次触发：宽限从头开始，不能因为"首次触发已过 50s"就直接 taker
    st, dec = _tick(st, mid=99.85, now_ts=t0 + 20.0, limits=_limits(grace=60.0))
    assert not any(f.is_flatten for f in dec.fills), "宽限必须重算 ✗"
    assert abs(float(st.stop_since or 0.0) - (t0 + 20.0)) < 1e-6
