# -*- coding: utf-8 -*-
"""[h625] 20 档价差选档：窄价差先不新开，没有深度不干预，停留期内不来回换。"""
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


def test_decision_dwell_holds():
    ok, until, opened = _mm.book_slot_decision(
        spread_bp=0.01, min_bp=1.0, now_ts=1000.0, until=0.0,
        is_open=True, dwell_sec=180.0,
    )
    assert ok is False and opened is False and until == 1180.0
    # 停留期内价差变宽也不立刻打开
    ok2, until2, opened2 = _mm.book_slot_decision(
        spread_bp=5.0, min_bp=1.0, now_ts=1100.0, until=until,
        is_open=opened, dwell_sec=180.0,
    )
    assert ok2 is False and until2 == until
    # 变宽不再锁死 3 分钟：下一拍变窄立刻停
    ok_w, until_w, open_w = _mm.book_slot_decision(
        spread_bp=2.5, min_bp=1.0, now_ts=2000.0, until=0.0,
        is_open=False, dwell_sec=180.0,
    )
    assert ok_w is True and open_w is True
    ok_t, _, open_t = _mm.book_slot_decision(
        spread_bp=0.2, min_bp=1.0, now_ts=2001.0, until=until_w,
        is_open=open_w, dwell_sec=180.0,
    )
    assert ok_t is False and open_t is False
    # 没有深度、也不在停留期 ⇒ 允许
    ok3, _, _ = _mm.book_slot_decision(
        spread_bp=None, min_bp=1.0, now_ts=2000.0, until=0.0,
        is_open=True, dwell_sec=180.0,
    )
    assert ok3 is True


def _limits(**kw):
    base = dict(
        trend_pause_bp=0.0, ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
        ofi_require_threshold=0.0, pullback_flow_block=0.0, vwap_revert_bp=0.0,
        vwap_flow_block=0.0, trend_add_block_bp=0.0, trend_add_block_q=0.0,
        entry_block_symbols=None, inv_add_block_ratio=0.0,
        max_net_directional_ratio=0.15, sudden_move_bp=0.0,
        be_mult=0.0, jump_pause_bp=0.0, markout_window_n=0,
        book_slot_min_bp=0.0, book_slot_dwell_sec=180.0,
    )
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(symbol="BTC", qty=0.0, depth=None, now_ts=1_000_030.0, **limkw):
    st = _mm.SymbolState(symbol=symbol)
    st.mid_hist = [100.0] * 21
    book = InventoryBook()
    if qty:
        book.positions[symbol] = Position(
            qty=qty, avg_px=100.0, avg_mid=100.0, opened_ts=1_000_000.0,
            last_ts=1_000_000.0,
        )
        st.qty = qty
    dec, _ = _mm.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now_ts,
        equity=1000.0, fill_notional=100.0, half_spread=0.005,
        params=QuoteParams(), limits=_limits(**limkw), book=book,
        depth_spread_bp=depth,
    )
    return dec, st


def test_tight_book_blocks_flat_keeps_reduce():
    flat, _ = _tick(depth=0.01, book_slot_min_bp=1.0)
    assert flat.bid == 0 and flat.ask == 0
    assert flat.skip == "book_tight"
    long, _ = _tick(qty=1.0, depth=0.01, book_slot_min_bp=1.0)
    assert long.bid == 0 and long.ask > 0


def test_wide_book_and_missing_depth_still_quote():
    wide, _ = _tick(depth=2.5, book_slot_min_bp=1.0)
    assert wide.bid > 0 and wide.ask > 0
    missing, _ = _tick(depth=None, book_slot_min_bp=1.0)
    assert missing.bid > 0 and missing.ask > 0


def test_off_by_default():
    dec, _ = _tick(depth=0.01, book_slot_min_bp=0.0)
    assert dec.bid > 0 and dec.ask > 0
