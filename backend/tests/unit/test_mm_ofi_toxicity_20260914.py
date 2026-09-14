# -*- coding: utf-8 -*-
"""[2026-09-14 F86] 流向毒性闸契约（学术升级：OI 加仓闸；择时平仓已验证为负、不启用）。"""
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


def _state():
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = [100.0 + 0.001 * i for i in range(80)]
    return st


def _tick(st, book, ofi, limits):
    return mmrunner.plan_tick(
        state=st, mid=100.08, seg_low=100.07, seg_high=100.09,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, params=QuoteParams(),
        limits=limits, book=book, ofi=ofi,
    )[0]


def _book(qty=0.0):
    b = InventoryBook()
    if qty:
        b.positions["BTC"] = Position(qty=qty, avg_px=100.0, avg_mid=100.0,
                                      opened_ts=1_000_000.0, last_ts=1_000_000.0)
    return b


def test_ofi_off_by_default():
    """默认关闭（threshold=0）⇒ 行为与旧版一致（两侧都挂）。"""
    dec = _tick(_state(), _book(), ofi=-0.9, limits=LaneRiskLimits())
    assert dec.bid > 0 and dec.ask > 0


def test_ofi_sell_pressure_blocks_bid():
    """卖压（OFI<-0.5）⇒ 封锁买单（逆势加仓侧），卖侧照常。"""
    dec = _tick(_state(), _book(), ofi=-0.8,
                limits=LaneRiskLimits(ofi_block_threshold=0.5))
    assert dec.bid == 0, "卖压时应封买"
    assert dec.ask > 0
    assert "ofi_toxic_sell" in (dec.skip or "")


def test_ofi_buy_pressure_blocks_ask():
    """买压（OFI>+0.5）⇒ 封锁卖单，买侧照常。"""
    dec = _tick(_state(), _book(), ofi=0.8,
                limits=LaneRiskLimits(ofi_block_threshold=0.5))
    assert dec.ask == 0
    assert dec.bid > 0
    assert "ofi_toxic_buy" in (dec.skip or "")


def test_ofi_reduce_side_exempt():
    """减仓侧豁免（F76 语义）：空头遇卖压时买=回补，必须放行。"""
    dec = _tick(_state(), _book(qty=-0.5), ofi=-0.8,
                limits=LaneRiskLimits(ofi_block_threshold=0.5))
    assert dec.bid > 0, "空头回补侧不应被封"


def test_ofi_flatten_default_off():
    """择时平仓默认关闭（30 天回放实测 -13.8%，已否决）。"""
    lim = LaneRiskLimits()
    assert lim.ofi_flatten_threshold == 0.0
    assert lim.ofi_block_threshold == 0.0


def test_plan_tick_accepts_ofi_and_fetch_exposes_it():
    """实盘取数必须给出 ofi 字段，plan_tick 必须接收。"""
    import inspect
    src = inspect.getsource(mmrunner.ShadowRunner.fetch_market)
    assert '"ofi"' in src
    src2 = inspect.getsource(mmrunner.plan_tick)
    assert "ofi" in src2 and "ofi_block_threshold" in src2
