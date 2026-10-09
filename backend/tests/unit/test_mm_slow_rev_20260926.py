# -*- coding: utf-8 -*-
"""[h341 2026-09-26] `side_mode="slow_rev"`（15 分钟反转备选规则）的回归测试。

信号：|r900|（60 期 × 15s = 900s）≥ slow_rev_min_bp ⇒ 只挂反向（fade）侧。
- 涨过头（r900 > +40bp）⇒ 封买（顺势加仓/接刀），卖放行（fade）；
- 跌过头（r900 < −40bp）⇒ 封卖，买放行；
- |r900| < 40bp ⇒ 双边照旧（不受本分支影响）；
- 减仓侧豁免（F76 语义）：多头在跌过头时仍可卖（减仓）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    LaneRiskLimits,
    Position,
    QuoteParams,
)


def _mk_state(hist):
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = list(hist)
    return st


def _mk_book(qty=0.0):
    book = InventoryBook()
    if qty:
        book.positions["BTC"] = Position(
            qty=qty, avg_px=100.0, avg_mid=100.0, opened_ts=1_000_000.0,
            last_ts=1_000_000.0)
    return book


def _limits():
    return LaneRiskLimits(slow_rev_min_bp=40.0, trend_pause_bp=0.0, vpin_pause_threshold=0.0)


def _params():
    import dataclasses
    return dataclasses.replace(QuoteParams(), side_mode="slow_rev")


def test_slow_rev_up_blocks_buy_allows_sell():
    """涨过头（r900≈+45bp）：封买、放卖。"""
    st = _mk_state([100.0 + 0.025 * i for i in range(61)])   # +1.5 总 → 60 期 ≈ +45bp
    dec, _ = mmrunner.plan_tick(
        state=st, mid=101.5, seg_low=101.4, seg_high=101.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits())
    assert dec.ask > 0, f"fade 卖侧应放行，skip={dec.skip} side={dec.skip_side}"
    assert "slow_rev_up" in (dec.skip or ""), dec.skip


def test_slow_rev_down_blocks_sell_allows_buy():
    """跌过头（r900≈−45bp）：封卖、放买。"""
    st = _mk_state([100.0 - 0.025 * i for i in range(61)])
    dec, _ = mmrunner.plan_tick(
        state=st, mid=98.5, seg_low=98.4, seg_high=98.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits())
    assert dec.bid > 0, f"fade 买侧应放行，skip={dec.skip} side={dec.skip_side}"
    assert "slow_rev_down" in (dec.skip or ""), dec.skip


def test_slow_rev_flat_both_sides_ok():
    """|r900| < 40bp：双边照旧（本分支不动作）。"""
    st = _mk_state([100.0 + 0.001 * i for i in range(61)])   # 60 期 ≈ +1.8bp
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.06, seg_low=100.0, seg_high=100.12,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits())
    assert "slow_rev" not in (dec.skip or ""), dec.skip


def test_slow_rev_reduce_side_exempt():
    """多头 + 跌过头：卖（多头减仓侧）豁免放行；买（fade 信号侧）也放行——
    封的只是"顺势加仓侧"（空头在此加卖）。"""
    st = _mk_state([100.0 - 0.025 * i for i in range(61)])
    book = _mk_book(qty=0.5)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=98.5, seg_low=98.4, seg_high=98.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits(), book=book)
    assert dec.ask > 0, f"多头减仓卖应放行，skip={dec.skip} side={dec.skip_side}"
    assert dec.bid > 0, "跌过头时 fade 买（信号侧）应放行"

    # 空头 + 跌过头：卖=顺势加仓侧 → 封；买=回补侧 → 放行
    book2 = _mk_book(qty=-0.5)
    dec2, _ = mmrunner.plan_tick(
        state=st, mid=98.5, seg_low=98.4, seg_high=98.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits(), book=book2)
    assert dec2.ask == 0, "跌过头时空头的加仓卖应被封"
    assert "slow_rev_down" in (dec2.skip or ""), dec2.skip
    assert dec2.bid > 0, "空头回补买（减仓侧）应放行"
