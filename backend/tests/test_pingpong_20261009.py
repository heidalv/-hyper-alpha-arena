# -*- coding: utf-8 -*-
"""[2026-10-09 重复来回做市] ping-pong 决策契约测试。

锁定用户设计里的规矩：
  1. 空仓双侧挂单：买单 = 买一 − 1 tick，卖单 = 卖一 + 1 tick，永不进价差。
  2. 进场单穿透前撤：前档量掉到武装时的一小半、或中价穿过挂单价 ⇒ 先撤不成交。
  3. 一进一出：成交后只挂反向同数量平仓单；离场单不撤；有仓禁止同向加仓。
  4. 吃单只留两种：盘口死了（taker_no_book）与真跳空（taker_stop）；
     没有任何时间性市价强平（8 分钟默认值已删）。
  5. 每笔一样大：平仓数量 = 开仓数量；空仓后歇息再挂下一对。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.services.market_maker import pingpong as PP
from backend.services.market_maker.core import InventoryBook, Position
from backend.services.market_maker.runner import PlannedFill, SymbolState, TickDecision

TMP = Path(tempfile.mkdtemp(prefix="pp_test_"))


def _book(state=None):
    b = InventoryBook()
    if state is not None and abs(state.qty) > 1e-12:
        b.positions[state.symbol] = Position(
            qty=state.qty, avg_px=state.avg_px, avg_mid=state.avg_mid,
            opened_ts=state.opened_ts, last_ts=state.last_ts)
    return b


def _call(state, *, bid, ask, now_ts, book=None, dec=None, mid=None,
          seg_low=0.0, seg_high=0.0, seg_sell=0.0, seg_buy=0.0,
          bid_qty=0.0, ask_qty=0.0, vol_at_bid=0.0, vol_at_ask=0.0,
          equity=10_000.0, vol_300s_bp=5.0, regime="R1", **over):
    book = book if book is not None else _book(state)
    dec = dec if dec is not None else TickDecision(symbol=state.symbol)
    if mid is None:
        mid = ((bid + ask) / 2.0) if (bid > 0 and ask > 0) else 100.0
    kw = dict(
        state=state, mid=mid, bb=bid, ba=ask, now_ts=now_ts,
        taker_fee_bp=4.0, local_book=book, dec=dec, PlannedFill=PlannedFill,
        regime=regime, equity=equity, vol_300s_bp=vol_300s_bp,
        seg_low=seg_low, seg_high=seg_high, seg_sell=seg_sell, seg_buy=seg_buy,
        bid_qty=bid_qty, ask_qty=ask_qty, vol_at_bid=vol_at_bid,
        vol_at_ask=vol_at_ask, roundtrip_root=TMP,
        stop_floor_bp=15.0, stop_cap_bp=40.0, loss_frac=0.005, same_side_n=1,
    )
    kw.update(over)
    PP.pingpong_decision(**kw)
    return dec, book, state


# ══ 1. 进场价位：双侧后一档，永不进价差 ══════════════════════════

def test_flat_quotes_both_sides_behind_one_tick(monkeypatch):
    st = SymbolState(symbol="BTC")
    dec, _, _ = _call(st, bid=100.0, ask=100.1, now_ts=1000.0,
                      bid_qty=10, ask_qty=8)
    assert dec.bid == pytest.approx(99.9), "买单 = 买一 − 1 tick"
    assert dec.ask == pytest.approx(100.2), "卖单 = 卖一 + 1 tick"
    assert dec.bid <= 100.0 and dec.ask >= 100.1, "永不进价差"
    assert dec.bid_qty > 0 and dec.ask_qty > 0
    assert st.qty == 0 and dec.fills == []
    assert dec.action == "quote"


def test_one_tick_spread_still_outside(monkeypatch):
    """价差只有一个 tick 时也不能插进去：买 < 买一、卖 > 卖一。"""
    st = SymbolState(symbol="UNI")
    dec, _, _ = _call(st, bid=100.0, ask=100.001, now_ts=1000.0,
                      bid_qty=10, ask_qty=10)
    assert dec.bid == pytest.approx(99.999)
    assert dec.ask == pytest.approx(100.002)
    assert dec.bid < 100.0 and dec.ask > 100.001


def test_missing_tick_quotes_at_touch_not_inside(monkeypatch):
    """交易所步进缺失 ⇒ 退化为挂买一/卖一（仍不越界）。"""
    st = SymbolState(symbol="TEST")
    dec, _, _ = _call(st, bid=99.9, ask=100.1, now_ts=1000.0)
    assert dec.bid == pytest.approx(99.9)
    assert dec.ask == pytest.approx(100.1)


# ══ 2. 成交前撤单（只对进场单）══════════════════════════════════

def test_thin_front_cancels_entry_before_fill(monkeypatch):
    st = SymbolState(symbol="BTC")
    dec1, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1000.0,
                           bid_qty=10, ask_qty=8)
    assert st.quote_bid == pytest.approx(99.9)
    assert st.pp_front0_bid == 10.0
    # 前档 10 → 3（< 5），且本拍价格打到挂单价 ⇒ 必须先撤，不得成交
    dec2, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1010.0,
                           bid_qty=3, ask_qty=8, book=book,
                           seg_low=99.9, seg_high=99.9, seg_sell=5, seg_buy=0,
                           vol_at_bid=5)
    assert st.qty == 0, "前档变薄的那一拍不得成交（先撤再判）"
    assert dec2.fills == []
    # 撤后立刻按新参考重新挂回同档
    assert st.quote_bid == pytest.approx(99.9)
    assert st.pp_front0_bid == 3.0
    assert dec2.skip.startswith("pp_cancel")


def test_mid_crossed_cancels_entry_before_fill(monkeypatch):
    st = SymbolState(symbol="BTC")
    _call(st, bid=100.0, ask=100.1, now_ts=1000.0, bid_qty=10, ask_qty=8)
    assert st.quote_bid == pytest.approx(99.9)
    # 盘口下移：新买一 99.8 / 卖一 99.9 ⇒ 中价 99.85 穿过 99.9
    dec2, book, st = _call(st, bid=99.8, ask=99.9, now_ts=1010.0,
                           bid_qty=10, ask_qty=8,
                           seg_low=99.8, seg_high=99.9, seg_sell=5, seg_buy=0,
                           vol_at_bid=5)
    assert st.qty == 0 and dec2.fills == [], "中价穿过的那一拍不得成交"
    assert st.quote_bid == pytest.approx(99.7), "重新挂到新买一后面一档"
    assert st.quote_bid <= 99.8


# ══ 3. 一进一出 ═════════════════════════════════════════════════

def _open_long(state, book):
    """排队吃完（vol 5+8 > 10）⇒ 买单成交，返回成交后的 dec。"""
    _call(state, bid=100.0, ask=100.1, now_ts=1000.0, book=book,
          bid_qty=10, ask_qty=8)
    dec2, book, st = _call(state, bid=100.0, ask=100.1, now_ts=1010.0,
                           book=book, bid_qty=10, ask_qty=8,
                           seg_low=99.9, seg_high=99.9, seg_sell=5, seg_buy=0,
                           vol_at_bid=5)
    assert st.qty == 0 and st.pp_cum_bid == 5.0, "队列没吃完不成交"
    dec3, book, st = _call(state, bid=100.0, ask=100.1, now_ts=1020.0,
                           book=book, bid_qty=10, ask_qty=8,
                           seg_low=99.9, seg_high=99.9,
                           seg_sell=8, seg_buy=0, vol_at_bid=8)
    return dec3, book, st


def test_queue_then_entry_then_reverse_exit_same_qty(monkeypatch):
    st = SymbolState(symbol="BTC")
    book = _book()
    dec3, book, st = _open_long(st, book)
    assert st.qty > 0, "队列吃完 ⇒ 成交"
    f = dec3.fills[-1]
    assert f.side == "buy" and f.fee_usd == 0.0
    assert dec3.exit_path == "pp_entry"
    qty_entry = st.qty
    # 同拍只挂反向平仓：卖 @ 100.2，数量 = 持仓（与开仓同数量）
    assert dec3.ask == pytest.approx(100.2)
    assert dec3.ask_qty == pytest.approx(qty_entry)
    assert dec3.bid == 0.0, "有仓时不得再挂进场侧"
    # 卖侧被打到 ⇒ 平仓，rt_bp 入账，随后歇息
    dec4, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1030.0, book=book,
                           bid_qty=10, ask_qty=8,
                           seg_low=100.0, seg_high=100.2, seg_sell=0, seg_buy=5,
                           vol_at_ask=5)
    assert st.qty == 0
    ef = dec4.fills[-1]
    assert ef.side == "sell" and ef.is_flatten
    assert ef.rt_bp is not None and ef.rt_bp > 0
    assert ef.fee_usd == 0.0
    assert dec4.exit_path == "pp_exit"
    assert st.pp_rest_until == pytest.approx(1030.0 + 15.0)


def test_exit_quote_never_cancelled_and_no_time_taker(monkeypatch):
    """离场单不因薄量/价格变化被撤；持仓再老也没有时间性吃单。"""
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                     opened_ts=500.0, opened_ts_true=500.0)
    book = _book(st)
    dec, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1000.0, book=book)
    assert st.qty > 0 and dec.fills == []
    assert dec.ask == pytest.approx(100.2), "平仓单挂在卖一后面一档"
    assert dec.ask_qty == pytest.approx(50.0)
    assert dec.skip == "pp_exit"
    # 下一拍前档归零、价格不动：离场单必须还在（不撤、不吃单）
    dec2, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1010.0,
                           book=book, bid_qty=0, ask_qty=0)
    assert st.qty > 0 and dec2.fills == []
    assert dec2.ask == pytest.approx(100.2)
    # 持仓已 500s（> 8 分钟）：健康盘口下仍是挂单离场，没有任何时间强平
    assert dec2.exit_path == "pp_exit"


def test_no_same_side_add_while_in_position(monkeypatch):
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                     opened_ts=900.0, opened_ts_true=900.0)
    book = _book(st)
    dec, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1000.0, book=book,
                          seg_low=99.9, seg_high=99.9, seg_sell=10, seg_buy=0,
                          vol_at_bid=10)
    assert st.qty == 50.0, "有仓时买单侧被打到也不得加仓"
    assert dec.fills == []
    assert dec.bid == 0.0 and dec.ask == pytest.approx(100.2)
    # 残留的同侧进场挂单（不该有）也必须被清掉而不是成交
    st2 = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                      opened_ts=900.0, opened_ts_true=900.0)
    st2.quote_bid = 99.9
    book2 = _book(st2)
    dec2, book2, st2 = _call(st2, bid=100.0, ask=100.1, now_ts=1000.0,
                             book=book2, seg_low=99.9, seg_high=99.9,
                             seg_sell=10, seg_buy=0, vol_at_bid=10)
    assert st2.quote_bid == 0.0 and st2.qty == 50.0 and dec2.fills == []


# ══ 4. 吃单只留两种 ═════════════════════════════════════════════

def test_deep_gap_is_taker_stop_with_fee(monkeypatch):
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                     opened_ts=900.0, opened_ts_true=900.0)
    book = _book(st)
    # 买一 97 ⇒ 浮亏 ≈ −304bp，越过灾难止损档 ⇒ 吃单
    dec, book, st = _call(st, bid=97.0, ask=97.2, now_ts=1000.0, book=book)
    assert st.qty == 0
    f = dec.fills[-1]
    assert dec.exit_path == "taker_stop"
    assert f.fee_usd < 0, "吃单必须计费"
    assert f.rt_bp is not None and f.rt_bp < 0


def test_dead_book_is_taker_no_book_after_300s(monkeypatch):
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0, avg_mid=100.0,
                     opened_ts=500.0, opened_ts_true=500.0)
    book = _book(st)
    dec, book, st = _call(st, bid=0.0, ask=0.0, now_ts=1000.0, book=book)
    assert st.qty == 0
    assert dec.exit_path == "taker_no_book"
    assert dec.fills[-1].fee_usd < 0


def test_ordinary_swing_is_maker_exit_not_taker(monkeypatch):
    """普通反向晃动（没到深跳档）⇒ 继续挂单，绝不普通吃单。"""
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                     opened_ts=900.0, opened_ts_true=900.0)
    book = _book(st)
    dec, book, st = _call(st, bid=99.8, ask=99.9, now_ts=1000.0, book=book)
    assert st.qty > 0 and dec.fills == []
    assert dec.ask == pytest.approx(100.0), "空头侧挂平仓 = 卖一 + 1 tick"


# ══ 5. 歇息与闸门 ══════════════════════════════════════════════

def test_rest_pause_then_requote_both_sides(monkeypatch):
    monkeypatch.setenv("MM_PP_REST_SEC", "10")
    st = SymbolState(symbol="BTC")
    book = _book()
    dec3, book, st = _open_long(st, book)
    assert st.qty > 0
    _call(st, bid=100.0, ask=100.1, now_ts=1030.0, book=book,
          bid_qty=10, ask_qty=8,
          seg_low=100.0, seg_high=100.2, seg_sell=0, seg_buy=5, vol_at_ask=5)
    assert st.qty == 0
    dec_r, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1035.0, book=book)
    assert dec_r.bid == 0 and dec_r.ask == 0
    assert dec_r.skip == "pp_rest"
    dec_q, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1041.0, book=book)
    assert dec_q.bid == pytest.approx(99.9)
    assert dec_q.ask == pytest.approx(100.2)


def test_r45_blocks_entry_but_exit_keeps_resting(monkeypatch):
    st = SymbolState(symbol="BTC", qty=50.0, avg_px=100.0,
                     opened_ts=900.0, opened_ts_true=900.0)
    book = _book(st)
    dec, book, st = _call(st, bid=100.0, ask=100.1, now_ts=1000.0,
                          regime="R4", book=book)
    assert st.qty > 0 and dec.fills == []
    assert dec.ask == pytest.approx(100.2), "R4 只挡进场，离场照挂（不吃单）"
    dec2, _, _ = _call(SymbolState(symbol="BTC"), bid=100.0, ask=100.1,
                       now_ts=1000.0, regime="R4")
    assert dec2.bid == 0 and dec2.ask == 0
    assert dec2.skip.startswith("regime_")


def test_flow_exit_only_blocks_entry(monkeypatch):
    dec, _, _ = _call(SymbolState(symbol="BTC"), bid=100.0, ask=100.1,
                      now_ts=1000.0, flow_exit_only=True)
    assert dec.bid == 0 and dec.ask == 0
    assert dec.skip == "flow_exit_only(pp)"


# ══ 默认路径与回滚 ═══════════════════════════════════════════════

def test_pingpong_is_default_via_active_flow(monkeypatch):
    """MM_PINGPONG 默认开启：active_flow_decision 委托到 ping-pong。"""
    monkeypatch.delenv("MM_PINGPONG", raising=False)
    from backend.services.market_maker.active_flow import active_flow_decision
    st = SymbolState(symbol="BTC")
    book = InventoryBook()
    dec = TickDecision(symbol="BTC")
    active_flow_decision(
        state=st, mid=100.05, ofi=0.0, trend_bp=0.0, bb=100.0, ba=100.1,
        now_ts=1000.0, fill_notional=0.0, taker_fee_bp=4.0, sl_bp=40.0,
        tp_bp=60.0, max_hold_sec=90.0, flow_thresh=0.15, local_book=book,
        dec=dec, PlannedFill=PlannedFill, maker_fee_bp=0.0, allow_entry=False,
        model_side=None, model_mu=None, regime="R1", equity=10_000.0,
        vol_300s_bp=8.0, roundtrip_root=TMP)
    assert dec.bid > 0 and dec.ask > 0, "默认走 ping-pong：双侧挂单"
    assert dec.bid <= 100.0 and dec.ask >= 100.1


def test_master_rollback_env_restores_legacy(monkeypatch):
    monkeypatch.setenv("MM_PINGPONG", "0")
    from backend.services.market_maker.active_flow import active_flow_decision
    st = SymbolState(symbol="BTC")
    book = InventoryBook()
    dec = TickDecision(symbol="BTC")
    active_flow_decision(
        state=st, mid=100.0, ofi=0.8, trend_bp=10.0, bb=99.9, ba=100.1,
        now_ts=1000.0, fill_notional=9_999.0, taker_fee_bp=4.0, sl_bp=40.0,
        tp_bp=60.0, max_hold_sec=90.0, flow_thresh=0.15, local_book=book,
        dec=dec, PlannedFill=PlannedFill, maker_fee_bp=0.0, allow_entry=True,
        model_side="buy", model_mu=2.5, regime="R1", equity=10_000.0,
        vol_300s_bp=8.0, roundtrip_root=TMP)
    assert dec.bid > 0 and dec.ask == 0, "回滚后恢复旧单边流（只挂一侧）"
