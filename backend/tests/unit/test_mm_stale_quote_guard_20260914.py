# -*- coding: utf-8 -*-
"""[2026-09-14 F89a] 陈旧挂单保护契约。

现场事故：币种重新加入宇宙时，运行态残留数天前的挂单；首个 tick 把当前区间成交
判成这些旧价位的成交——4 笔幻影成交、净敞口 -$739（上限 $300），且账本用旧
ref_mid 记成 +8~+12bp 假盈利。修：挂单年龄 > max_quote_age_sec ⇒ 丢弃挂单、不判成交。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits,
    QuoteParams,
)


def _mk_state(quote_ts: float, bid: float = 99.0, ask: float = 101.0):
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = [100.0] * 60
    st.quote_bid, st.quote_ask, st.quote_ts = bid, ask, quote_ts
    st.quote_mid = 100.0
    return st


def _limits(age: float):
    """与线上一致的宽松敞口 + 指定挂单年龄阈值。"""
    return LaneRiskLimits(max_quote_age_sec=age, trend_pause_bp=0.0,
                          vol_pause_mult=0.0, vol_pause_sigma=0.0,
                          max_symbol_notional_ratio=1.0,
                          max_net_directional_ratio=1.0,
                          max_net_exposure_ratio=1.0)


def test_stale_quote_is_discarded_not_filled():
    """挂单年龄 300s > 阈值 90s ⇒ 不产生成交（旧价位不得成交）。

    这是 F89a 的核心保护：区间 90~110 会同时穿越陈旧买单(99)与卖单(101)，
    若不丢弃挂单就会产生两笔幻影成交。
    """
    st = _mk_state(quote_ts=1_000_000.0)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=90.0, seg_high=110.0,
        seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1_000_300.0,
        equity=300.0, fill_notional=300.0, params=QuoteParams(),
        limits=_limits(90.0),
    )
    assert not dec.fills, f"陈旧挂单不得成交: {dec.fills}"


def test_fresh_quote_still_fills():
    """挂单年龄 15s（新鲜）⇒ 照常判定成交（回归保护）。"""
    st = _mk_state(quote_ts=1_000_000.0)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=98.5, seg_high=100.5,
        seg_taker_sell=5.0, seg_taker_buy=0.0, now_ts=1_000_015.0,
        equity=300.0, fill_notional=300.0, params=QuoteParams(),
        limits=_limits(90.0),
    )
    assert dec.fills, "新鲜挂单应照常成交"


def test_disabled_when_zero():
    """阈值 0 = 关闭保护（旧行为逐字一致）。"""
    st = _mk_state(quote_ts=1_000_000.0)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=90.0, seg_high=110.0,
        seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1_000_300.0,
        equity=300.0, fill_notional=300.0, params=QuoteParams(),
        limits=_limits(0.0),
    )
    assert dec.fills, "关闭保护时按旧行为成交"


def test_default_threshold_is_conservative():
    """默认阈值存在且为分钟级（不是 0，也不至于把正常 tick 判成异常）。"""
    lim = LaneRiskLimits()
    assert 30.0 <= lim.max_quote_age_sec <= 600.0


def test_cross_symbol_directional_cap_blocks_second_entry():
    """[多标的敞口契约] 共享账本下第一笔建仓吃掉方向额度后，第二标的不再放行同向加仓。

    现场背景：F89a 幻影成交曾把净敞口推到 -$739（上限 $300）。新鲜挂单路径下
    共享账本必须在同一 tick 内即时生效——本测试锁定该性质。
    """
    from backend.services.market_maker.core import InventoryBook, Position

    book = InventoryBook()
    book.positions["BTC"] = Position(qty=-300.0 / 100.0, avg_px=100.0, avg_mid=100.0,
                                     opened_ts=1_000_000.0, last_ts=1_000_000.0)
    marks = {"BTC": 100.0, "ETH": 100.0}
    lim = LaneRiskLimits(max_symbol_notional_ratio=1.0,
                         max_net_directional_ratio=1.0,
                         max_net_exposure_ratio=1.0,
                         vol_pause_sigma=0.0)
    # BTC 已占用 -$300 方向额度（权益 300 × 1.0）
    st_eth = mmrunner.SymbolState(symbol="ETH")
    st_eth.mid_hist = [100.0] * 60
    dec, _ = mmrunner.plan_tick(
        state=st_eth, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=300.0, fill_notional=300.0, params=QuoteParams(),
        limits=lim, book=book, marks=marks,
    )
    # 卖（同向加仓）应被拒；买（反向=减仓方向）应放行
    assert dec.ask == 0 and "exposure" in (dec.skip or ""), (dec.ask, dec.skip)
    assert dec.bid > 0, "反向侧应放行（减仓语义）"
