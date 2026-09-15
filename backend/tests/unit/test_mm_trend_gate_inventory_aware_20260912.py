# -*- coding: utf-8 -*-
"""[2026-09-12 F76] L1 做市趋势闸库存感知契约。

现场：趋势闸（trend_pause_bp>0）把**减仓侧**一并封死——多头在上涨趋势里
挂不出卖单、空头在下跌趋势里挂不出买单，库存只能等超时 taker 平仓
（实盘平仓均价 -12.98bp，是亏损主因）。F76 修正：趋势闸只封**加仓侧**。

[F204 2026-09-15 **符号修正**] 本文件原先钉的是"**禁逆势侧**"（下跌禁买、上涨禁卖）✗。
该语义的依据（"亏损平仓来自下跌中买入"）被证伪：那是账本把持仓漂移记在**减仓腿**上
造成的归因假象（F187）✗。按 2915 笔账本 + 严格无未来函数分组：
    **顺势成交（买在上涨/卖在下跌）−4.053bp ✗   逆势成交 +2.109bp ✓  差 6.16bp**
⇒ 现在封的是**顺势侧**：上涨禁**买**、下跌禁**卖** ✓。
F76 的"减仓侧豁免"与本次符号修正**正交**，逻辑不变 ✓（断言同步更新）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
            last_ts=1_000_000.0,
        )
    return book


def _trend_limits():
    return LaneRiskLimits(trend_pause_bp=5.0, trend_lookback=20)


# 上涨趋势：近 20 期中价净移动 +50bp；下跌趋势：-50bp
_UP = [100.0 + 0.025 * i for i in range(21)]        # 100.0 → 100.5
_DOWN = [100.0 - 0.025 * i for i in range(21)]      # 100.0 → 99.5


def test_long_uptrend_reduce_side_still_quotes():
    """多头 + 上涨趋势：卖（减仓）侧必须仍可挂单（F76 放行 ✓，与符号修正无关）。"""
    st = _mk_state(_UP)
    book = _mk_book(qty=0.5)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_trend_limits(), book=book,
    )
    assert dec.ask > 0, f"减仓侧卖单应存在，实际 skip={dec.skip} skip_side={dec.skip_side}"
    assert not dec.fills


def test_flat_uptrend_buy_blocked():
    """空仓 + 上涨趋势：**买**侧必须被封（F204：买在上涨=顺势 ✗）。

    旧版本断言的是"卖侧被封" ✗ —— 而卖在上涨恰是实证里**赚钱**的那一侧 ✓。
    """
    st = _mk_state(_UP)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_trend_limits(),
    )
    assert dec.bid == 0, "空仓上涨时买侧应被封（别追涨 ✗）"
    assert "trend_up" in (dec.skip or ""), dec.skip
    assert dec.ask > 0, "卖侧应允许（卖在上涨 = 逆势 = 实证为正 ✓）"


def test_long_downtrend_both_sides_pass_because_sell_is_reducing():
    """多头 + 下跌趋势：顺势侧是"卖"，但它是**减仓侧** ⇒ 按 F76 豁免 ⇒ 两侧都放行。

    （买在下跌 = 逆势 = 实证 +2.1bp ✓ 本就该放行 ✓）
    """
    st = _mk_state(_DOWN)
    book = _mk_book(qty=0.5)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=99.5, seg_low=99.4, seg_high=99.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_trend_limits(), book=book,
    )
    assert dec.bid > 0, "买（逆势低吸）应放行"
    assert dec.ask > 0, "卖是减仓侧 ⇒ 必须放行（F76 核心）"


def test_short_downtrend_reduce_side_still_quotes():
    """空头 + 下跌趋势：买（回补）侧必须仍可挂单（F76 放行）。"""
    st = _mk_state(_DOWN)
    book = _mk_book(qty=-0.5)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=99.5, seg_low=99.4, seg_high=99.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_trend_limits(), book=book,
    )
    assert dec.bid > 0, f"回补侧买单应存在，实际 skip={dec.skip} skip_side={dec.skip_side}"
    assert not dec.fills


def test_short_uptrend_both_sides_pass_because_buy_is_reducing():
    """空头 + 上涨趋势：顺势侧是"买"，但它是**回补侧** ⇒ 豁免 ⇒ 两侧都放行。"""
    st = _mk_state(_UP)
    book = _mk_book(qty=-0.5)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_trend_limits(), book=book,
    )
    assert dec.bid > 0, "买是回补侧 ⇒ 必须放行（F76 核心）"
    assert dec.ask > 0, "卖（别杀跌 ✗ 的反面：上涨中卖=逆势）应放行"

