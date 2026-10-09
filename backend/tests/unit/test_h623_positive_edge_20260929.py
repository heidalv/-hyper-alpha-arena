# -*- coding: utf-8 -*-
"""[h623] 正收益升级：绝对库存偏斜 + 分币尾部停加仓 + 库存停加仓。"""
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
    compute_quote,
)


def test_inv_skew_abs_widens_add_side_not_multiplicative():
    """绝对 bp：无盘口钳制时买侧加宽约 4bp；有钳制时减仓侧收到 mid。"""
    p = QuoteParams(
        spread_mult=0.5, spread_cross_margin=0.05, k_inv=1.0,
        inv_skew_abs_bp=4.0, k_vol=0.0, k_trend=0.0,
    )
    mid = 100.0
    # 无盘口：不被 F280 夹回半价差
    flat = compute_quote(
        symbol="NEAR", mid=mid, spread_bp=1.0, best_bid=0.0, best_ask=0.0,
        inv_ratio=0.0, params=p,
    )
    long = compute_quote(
        symbol="NEAR", mid=mid, spread_bp=1.0, best_bid=0.0, best_ask=0.0,
        inv_ratio=1.0, params=p,
    )
    assert flat and long
    assert long.w_bid_bp >= flat.w_bid_bp + 3.5, (flat.w_bid_bp, long.w_bid_bp)
    assert long.w_ask_bp <= 0.05, long.w_ask_bp
    # 有盘口钳制：减仓侧仍应收到更近（ask 贴 mid），加仓侧停靠最优买
    bb, ba = 99.995, 100.005
    long_c = compute_quote(
        symbol="NEAR", mid=mid, spread_bp=1.0, best_bid=bb, best_ask=ba,
        inv_ratio=1.0, params=p,
    )
    assert long_c.ask <= mid + 1e-9
    assert abs(long_c.bid - bb) < 1e-6


def test_inv_skew_abs_zero_keeps_multiplicative():
    """inv_skew_abs_bp=0 ⇒ 仍走旧乘性 k_inv。"""
    p = QuoteParams(
        spread_mult=0.5, k_inv=1.0, inv_skew_abs_bp=0.0, k_vol=0.0, k_trend=0.0,
    )
    mid = 100.0
    bb, ba = 99.995, 100.005
    flat = compute_quote(
        symbol="X", mid=mid, spread_bp=1.0, best_bid=bb, best_ask=ba,
        inv_ratio=0.0, params=p,
    )
    long = compute_quote(
        symbol="X", mid=mid, spread_bp=1.0, best_bid=bb, best_ask=ba,
        inv_ratio=1.0, params=p,
    )
    assert long.w_bid_bp < 2.0, long.w_bid_bp
    assert long.w_bid_bp >= flat.w_bid_bp


def _state(symbol="NEAR", hist=None, ar300=None):
    st = _mm.SymbolState(symbol=symbol)
    st.mid_hist = list(hist if hist is not None else [100.0] * 21)
    if ar300 is not None:
        st.ar300_hist = list(ar300)
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
        max_net_directional_ratio=0.15,
    )
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(symbol="NEAR", qty=0.0, hist=None, ar300=None, equity=1000.0,
          fill_notional=100.0, **limkw):
    dec, _ = _mm.plan_tick(
        state=_state(symbol, hist, ar300), mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=equity, fill_notional=fill_notional,
        params=QuoteParams(inv_skew_abs_bp=4.0), limits=_limits(**limkw),
        book=_book(symbol, qty),
    )
    return dec


def test_entry_block_list_does_not_override_universe():
    """名单不再禁开仓：空仓必须能挂双边；有仓也不是 entry_block。"""
    flat = _tick(symbol="BNB", entry_block_symbols=["BNB", "XRP"])
    assert flat.bid > 0 and flat.ask > 0
    assert flat.skip != "entry_block"
    long = _tick(symbol="BNB", qty=1.0, entry_block_symbols=["BNB", "XRP"])
    assert long.skip != "entry_block"
    assert long.ask > 0


def test_inv_add_block_cancels_add_keeps_reduce():
    # qty=1、mid=100、limit = equity*0.15=150 ⇒ inv_ratio ≈ 100/150 ≈ 0.67 ≥ 0.25
    long = _tick(qty=1.0, equity=1000.0, inv_add_block_ratio=0.25)
    assert long.bid == 0 and long.ask > 0, (long.bid, long.ask, long.skip)
    flat = _tick(qty=0.0, inv_add_block_ratio=0.25)
    assert flat.bid > 0 and flat.ask > 0


def test_trend_add_block_q_uses_symbol_tail_not_global_30():
    # 该币历史 |r300| 都在 8bp；分位 0.9 ≈ 8；当前移动 ~12bp ≥ 8 ⇒ 停
    hist = [100.0 + 0.006 * i for i in range(21)]  # 20×0.006 = 0.12 → 12bp
    ar = [8.0] * 80
    blocked = _tick(hist=hist, ar300=ar, trend_add_block_bp=5.0, trend_add_block_q=0.9)
    assert blocked.bid == 0 and blocked.ask == 0, blocked.skip
    # 安静：当前只有 ~3bp < 绝对下限 5 ⇒ 不拦
    hist_quiet = [100.0 + 0.0015 * i for i in range(21)]  # 3bp
    ok = _tick(hist=hist_quiet, ar300=ar, trend_add_block_bp=5.0, trend_add_block_q=0.9)
    assert ok.bid > 0 and ok.ask > 0, ok.skip
