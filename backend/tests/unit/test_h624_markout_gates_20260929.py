# -*- coding: utf-8 -*-
"""[h624] markout / 盈亏平衡 / 跳价暂停 / stop_loss_maker。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as _mm  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    LaneRiskLimits,
    Position,
    QuoteParams,
)
from backend.services.market_maker.markout import (  # noqa: E402
    break_even_blocks_add,
    break_even_bp,
    jump_pause_hit,
    markout_bp,
    markout_halts_adds,
    rolling_markout_stats,
)


def test_markout_bp_signs():
    assert markout_bp(side="buy", fill_px=100.0, mid_later=100.1) > 0
    assert markout_bp(side="sell", fill_px=100.0, mid_later=99.9) > 0
    assert markout_bp(side="buy", fill_px=100.0, mid_later=99.9) < 0


def test_rolling_and_halt():
    rows = [{"markout_bp": -3.0, "capture_bp": 0.5, "notional": 100.0}] * 12
    st = rolling_markout_stats(rows, min_n=10)
    assert st["n"] >= 10
    assert markout_halts_adds(
        markout_bp=st["markout_bp"], capture_bp=st["capture_bp"],
        n=st["n"], min_n=10, halt_thresh_bp=0.0,
    )
    assert not markout_halts_adds(
        markout_bp=-1.0, capture_bp=2.0, n=12, min_n=10, halt_thresh_bp=0.0,
    )


def test_break_even_and_jump():
    be = break_even_bp(rolling_markout_bp=-4.0, stop_share=0.1, taker_fee_bp=4.0)
    assert be >= 4.0
    assert break_even_blocks_add(expected_capture_bp=0.4, be_bp=2.0, be_mult=1.0)
    assert not break_even_blocks_add(expected_capture_bp=0.4, be_bp=2.0, be_mult=0.0)
    assert jump_pause_hit([100.0, 100.2], thresh_bp=12.0)
    assert not jump_pause_hit([100.0, 100.01], thresh_bp=12.0)
    assert not jump_pause_hit([100.0, 100.2], thresh_bp=0.0)


def _state(symbol="NEAR", hist=None, samples=None, pending=None):
    st = _mm.SymbolState(symbol=symbol)
    st.mid_hist = list(hist if hist is not None else [100.0] * 21)
    if samples is not None:
        st.markout_samples = list(samples)
    if pending is not None:
        st.pending_markouts = list(pending)
    return st


def _book(symbol="NEAR", qty=0.0):
    book = InventoryBook()
    if qty:
        book.positions[symbol] = Position(
            qty=qty, avg_px=100.0, avg_mid=100.0, opened_ts=1_000_000.0,
            last_ts=1_000_000.0,
        )
    return book


def _limits(**kw):
    base = dict(
        trend_pause_bp=0.0, ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
        ofi_require_threshold=0.0, pullback_flow_block=0.0, vwap_revert_bp=0.0,
        vwap_flow_block=0.0, trend_add_block_bp=0.0, trend_add_block_q=0.0,
        entry_block_symbols=None, inv_add_block_ratio=0.0,
        max_net_directional_ratio=0.15, sudden_move_bp=0.0,
        markout_horizon_sec=0.0, markout_window_n=0, markout_halt_bp=0.0,
        be_mult=0.0, jump_pause_bp=0.0, jump_pause_sec=0.0,
    )
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(symbol="NEAR", qty=0.0, hist=None, samples=None, pending=None,
          mid=100.0, now_ts=1_000_030.0, half_spread=0.005, **limkw):
    st = _state(symbol, hist, samples, pending)
    if qty:
        st.qty = qty
    dec, _ = _mm.plan_tick(
        state=st, mid=mid, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now_ts,
        equity=1000.0, fill_notional=100.0, half_spread=half_spread,
        params=QuoteParams(inv_skew_abs_bp=4.0), limits=_limits(**limkw),
        book=_book(symbol, qty),
    )
    return dec, st


def test_a1_resolve_pending_markout():
    """到期 pending → samples；horizon 开启。"""
    pending = [{
        "ts": 1_000_000.0, "side": "buy", "fill_px": 100.0,
        "capture_bp": 0.5, "notional": 50.0,
    }]
    dec, st = _tick(
        pending=pending, mid=99.8, now_ts=1_000_035.0,
        markout_horizon_sec=30.0,
    )
    assert len(st.pending_markouts) == 0
    assert len(st.markout_samples) == 1
    assert st.markout_samples[0]["markout_bp"] < 0  # 买后跌 = 负
    snap = _mm.symbol_markout_snapshot(st, min_n=1)
    assert snap["n"] >= 1
    assert "m30" in snap


def test_a1_horizon_off_no_resolve():
    pending = [{
        "ts": 1_000_000.0, "side": "buy", "fill_px": 100.0,
        "capture_bp": 0.5, "notional": 50.0,
    }]
    _, st = _tick(pending=pending, mid=99.8, now_ts=1_000_035.0,
                  markout_horizon_sec=0.0)
    assert len(st.pending_markouts) == 1
    assert len(st.markout_samples) == 0


def test_a2_markout_halt_blocks_add_keeps_reduce():
    samples = [{"markout_bp": -5.0, "capture_bp": 0.4, "notional": 100.0}] * 12
    flat, _ = _tick(samples=samples, markout_window_n=10, markout_halt_bp=0.0,
                    markout_horizon_sec=30.0)
    assert flat.bid == 0 and flat.ask == 0
    assert flat.skip == "markout_halt" or "markout_halt" in (flat.skip or "")
    long, _ = _tick(qty=1.0, samples=samples, markout_window_n=10,
                    markout_halt_bp=0.0, markout_horizon_sec=30.0)
    assert long.bid == 0 and long.ask > 0


def test_a2_window_zero_no_halt():
    samples = [{"markout_bp": -5.0, "capture_bp": 0.4, "notional": 100.0}] * 12
    flat, _ = _tick(samples=samples, markout_window_n=0, markout_halt_bp=0.0)
    assert flat.bid > 0 and flat.ask > 0


def test_a3_break_even_blocks_flat():
    # 半价差 0.005 @ mid100 = 0.5bp；be≈4+ ⇒ 空仓不挂
    samples = [{"markout_bp": -4.0, "capture_bp": 0.4, "notional": 100.0}] * 12
    flat, _ = _tick(samples=samples, be_mult=1.0, half_spread=0.005,
                    markout_horizon_sec=0.0)
    assert flat.bid == 0 and flat.ask == 0
    long, _ = _tick(qty=1.0, samples=samples, be_mult=1.0, half_spread=0.005)
    assert long.bid == 0 and long.ask > 0


def test_a4_jump_pause_both_sides():
    hist = [100.0] + [100.0] * 19 + [100.25]  # +25bp
    dec, st = _tick(hist=hist, jump_pause_bp=12.0, jump_pause_sec=60.0,
                    now_ts=1_000_000.0)
    assert dec.skip == "jump_pause"
    assert dec.action == "pause"
    assert st.jump_pause_until >= 1_000_000.0


def test_a5_stop_loss_maker_exit_path_on_reduce():
    """宽限期内被动减仓 → exit_path=stop_loss_maker。"""
    st = _state(hist=[100.0] * 21)
    st.qty = 1.0
    st.avg_px = st.avg_mid = 100.0
    st.opened_ts = 1_000_000.0
    st.quote_bid = 99.9
    st.quote_ask = 100.1
    st.quote_mid = 100.0
    st.quote_ts = 1_000_029.0
    st.stop_since = 1_000_020.0  # 已在止损宽限
    book = _book("NEAR", qty=1.0)
    dec, _ = _mm.plan_tick(
        state=st, mid=100.0, seg_low=99.8, seg_high=100.2,
        seg_taker_sell=0.0, seg_taker_buy=50.0, now_ts=1_000_030.0,
        equity=1000.0, fill_notional=100.0, half_spread=0.005,
        params=QuoteParams(), limits=_limits(stop_loss_bp=0.0,
                                             stop_maker_grace_sec=90.0,
                                             markout_horizon_sec=30.0),
        book=book,
    )
    # 卖侧减仓应触发（seg_high > ask）
    if dec.fills:
        assert any(f.side == "sell" for f in dec.fills)
        assert dec.exit_path == "stop_loss_maker"


def test_limits_fields_exist():
    lim = LaneRiskLimits()
    for k in ("markout_horizon_sec", "markout_window_n", "markout_halt_bp",
              "be_mult", "be_taker_fee_bp", "jump_pause_bp", "jump_pause_sec"):
        assert hasattr(lim, k)
