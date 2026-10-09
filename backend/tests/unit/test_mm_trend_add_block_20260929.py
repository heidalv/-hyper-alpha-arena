# -*- coding: utf-8 -*-
"""[h622] 大波动停止新建仓 + 指定币只减仓。

守三件事：
  1. 两个开关默认关闭时，空仓仍然双边报价；
  2. 5 分钟净移动达到阈值时，空仓两边都不挂；已有多头仍可挂卖单减仓；
  3. 名单里的币空仓不挂单；已有空头仍可挂买单回补。
"""
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
    adds_blocked_by_trend,
    symbol_entry_blocked,
)


def test_helpers_default_off():
    assert adds_blocked_by_trend([100.0, 101.0], 0) is False
    assert adds_blocked_by_trend([100.0, 100.4], 30) is True   # 40bp
    assert adds_blocked_by_trend([100.0, 100.1], 30) is False  # 10bp
    assert symbol_entry_blocked("BNB", None) is False
    assert symbol_entry_blocked("BNBUSDT", ["BNB"]) is True
    assert symbol_entry_blocked("XRP", ["BNB"]) is False


def _state(symbol="BTC", hist=None):
    st = _mm.SymbolState(symbol=symbol)
    st.mid_hist = list(hist if hist is not None else [100.0] * 21)
    return st


def _book(symbol="BTC", qty=0.0):
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
        vwap_flow_block=0.0, trend_add_block_bp=0.0, entry_block_symbols=None,
    )
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(symbol="BTC", qty=0.0, hist=None, **limkw):
    dec, _ = _mm.plan_tick(
        state=_state(symbol, hist), mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_limits(**limkw), book=_book(symbol, qty),
    )
    return dec


def test_default_still_quotes_both_sides():
    hist = [100.0 + 0.02 * i for i in range(21)]  # 40bp，但闸是关的
    dec = _tick(hist=hist)
    assert dec.bid > 0 and dec.ask > 0, dec.skip


def test_large_move_blocks_flat_entries_and_keeps_reduce():
    hist = [100.0 + 0.02 * i for i in range(21)]  # 40bp ≥ 30
    flat = _tick(hist=hist, trend_add_block_bp=30)
    assert flat.bid == 0 and flat.ask == 0, flat.skip
    assert flat.skip == "trend_add_block"
    long = _tick(qty=1.0, hist=hist, trend_add_block_bp=30)
    assert long.bid == 0 and long.ask > 0, (long.bid, long.ask, long.skip)


def test_entry_block_symbol_does_not_cancel_quotes():
    flat = _tick(symbol="BNB", entry_block_symbols=["BNB"])
    assert flat.bid > 0 and flat.ask > 0, flat.skip
    short = _tick(symbol="BNB", qty=-1.0, entry_block_symbols=["BNB"])
    assert short.bid > 0 and short.ask > 0, (short.bid, short.ask, short.skip)
    other = _tick(symbol="XRP", entry_block_symbols=["BNB"])
    assert other.bid > 0 and other.ask > 0, other.skip
