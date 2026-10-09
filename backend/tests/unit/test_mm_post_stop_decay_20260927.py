# -*- coding: utf-8 -*-
"""[h395 2026-09-27] #16③ 止损后降腿量（post_stop_decay）回归测试。

规则：该币 30min 内发生过强制止损离场（stop_loss_taker / trail_lock_taker）
⇒ 加仓腿名义 ×(1−post_stop_decay)；减仓腿不受影响；0=关=旧行为。
依据：h394 影子测验（13 对连环止损，降半档 +$1.38/5h）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _limits(**kw):
    base = dict(stop_loss_bp=40.0, take_profit_bp=0.0, stop_maker_grace_sec=0.0,
                min_hold_seconds=0.0, reversal_decay_bp=0.0, trend_pause_bp=0.0,
                sudden_move_bp=0.0, ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
                max_one_side_seconds=3600.0)
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(st, mid, now, limits, fill_notional=100.0, seg_sell=100.0, seg_buy=0.0,
          seg_low=None, seg_high=None):
    return mmrunner.plan_tick(
        state=st, mid=mid,
        seg_low=seg_low if seg_low is not None else mid - 0.2,
        seg_high=seg_high if seg_high is not None else mid + 0.2,
        seg_taker_sell=seg_sell, seg_taker_buy=seg_buy, now_ts=now,
        equity=5000.0, fill_notional=fill_notional,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits)


def _flat_with_bid_quote(mid=100.0, now=1_000_100.0):
    """空仓 + 一张 15s 前挂出的买单（可被向下穿越判定成交）。"""
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.0
    st.quote_bid = 99.9
    st.quote_mid = mid
    st.quote_ts = now - 15.0
    return st


def test_decay_halves_add_fill_qty():
    """止损后 30min 内：加仓腿名义减半（qty = eff_notional/mid）。"""
    now = 1_000_100.0
    st = _flat_with_bid_quote(now=now)
    st.last_stop_ts = now - 60.0
    dec, _ = _tick(st, 100.0, now, _limits(post_stop_decay=0.5))
    fills = [f for f in dec.fills if not f.is_flatten]
    assert fills, f"应有加仓腿成交，skip={dec.skip}"
    assert abs(fills[0].qty - 0.5) < 1e-6, f"期望 0.5（100×0.5/100），实际 {fills[0].qty}"


def test_decay_off_full_qty():
    """post_stop_decay=0：旧行为逐字一致（qty = fill_notional/mid = 1.0）。"""
    now = 1_000_100.0
    st = _flat_with_bid_quote(now=now)
    st.last_stop_ts = now - 60.0
    dec, _ = _tick(st, 100.0, now, _limits(post_stop_decay=0.0))
    fills = [f for f in dec.fills if not f.is_flatten]
    assert fills and abs(fills[0].qty - 1.0) < 1e-6, f"期望 1.0，实际 {[f.qty for f in fills]}"


def test_decay_expires_after_window():
    """止损已过 30min：恢复满额腿量。"""
    now = 1_000_100.0
    st = _flat_with_bid_quote(now=now)
    st.last_stop_ts = now - 2000.0
    dec, _ = _tick(st, 100.0, now, _limits(post_stop_decay=0.5))
    fills = [f for f in dec.fills if not f.is_flatten]
    assert fills and abs(fills[0].qty - 1.0) < 1e-6, f"期望 1.0，实际 {[f.qty for f in fills]}"


def test_reduce_leg_unaffected_by_decay():
    """减仓腿精确平掉现有仓位（F91），不受衰减影响。"""
    now = 1_000_100.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.3
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 120.0
    st.last_stop_ts = now - 60.0
    st.quote_ask = 100.1
    st.quote_mid = 100.0
    st.quote_ts = now - 15.0
    dec, _ = _tick(st, 100.0, now, _limits(post_stop_decay=0.5),
                   seg_buy=100.0, seg_sell=0.0, seg_low=99.8, seg_high=100.3)
    fills = [f for f in dec.fills if not f.is_flatten]
    assert fills, f"减仓腿应有成交，skip={dec.skip}"
    assert abs(fills[0].qty - 0.3) < 1e-6, f"减仓应精确平仓 0.3，实际 {fills[0].qty}"


def test_stop_sets_last_stop_ts():
    """强制止损离场时刻写入 last_stop_ts（衰减窗起点）。"""
    now = 1_000_100.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 120.0
    dec, _ = _tick(st, 99.5, now, _limits(post_stop_decay=0.5))
    assert any(f.is_flatten for f in dec.fills), f"−50bp 应触发止损，skip={dec.skip}"
    assert dec.exit_path == "stop_loss_taker"
    assert abs(st.last_stop_ts - now) < 1e-6, f"last_stop_ts 应=触发时刻，实际 {st.last_stop_ts}"


def test_serialization_preserves_last_stop_ts():
    st = _flat_with_bid_quote()
    st.last_stop_ts = 1234567.5
    st2 = mmrunner.SymbolState.from_dict(st.to_dict())
    assert abs(st2.last_stop_ts - 1234567.5) < 1e-9
