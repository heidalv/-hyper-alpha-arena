# -*- coding: utf-8 -*-
"""[h354 2026-09-27] P2 形态（VWAP 回归）实盘试跑框架的回归测试。

语义：|现价 − 60s VWAP| ≥ vwap_revert_bp ⇒ 只挂向 VWAP 回归的一侧；
减仓侧豁免（F76）。
"""
from __future__ import annotations

import dataclasses
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


def _mk_state():
    return mmrunner.SymbolState(symbol="BTC")


def _mk_book(qty=0.0):
    book = InventoryBook()
    if qty:
        book.positions["BTC"] = Position(
            qty=qty, avg_px=100.0, avg_mid=100.0, opened_ts=1_000_000.0,
            last_ts=1_000_000.0)
    return book


def _limits():
    return LaneRiskLimits(vwap_revert_bp=2.0, trend_pause_bp=0.0, vpin_pause_threshold=0.0)


def _params():
    return dataclasses.replace(QuoteParams(), side_mode="model")


def test_above_vwap_blocks_buy_allows_sell():
    """价在 VWAP 上方 ≥2bp：封买（回归向下）、卖放行。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits(), vwap60=100.0)
    assert dec.ask > 0, f"卖（回归方向）应放行，skip={dec.skip} side={dec.skip_side}"
    assert dec.bid == 0, "价高于 VWAP 时买应被封"
    assert "vwap_revert_up" in (dec.skip or ""), dec.skip


def test_below_vwap_blocks_sell_allows_buy():
    """价在 VWAP 下方 ≥2bp：封卖（回归向上）、买放行。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=98.0, seg_low=97.9, seg_high=98.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits(), vwap60=100.0)
    assert dec.bid > 0, f"买（回归方向）应放行，skip={dec.skip} side={dec.skip_side}"
    assert dec.ask == 0, "价低于 VWAP 时卖应被封"
    assert "vwap_revert_down" in (dec.skip or ""), dec.skip


def test_within_threshold_both_sides():
    """偏离 <2bp：双侧不受影响。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=100.01, seg_low=99.99, seg_high=100.03,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits(), vwap60=100.0)
    assert "vwap_revert" not in (dec.skip or ""), dec.skip


def test_reduce_side_exempt():
    """多头 + 价在 VWAP 上方：卖=减仓侧放行（豁免），买=加仓被封。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=_limits(), book=_mk_book(qty=0.5), vwap60=100.0)
    assert dec.ask > 0, "多头减仓卖应放行"
    assert dec.bid == 0, "多头加仓买应被封"


def test_gate_off_when_param_zero():
    """vwap_revert_bp=0：完全不动作。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=_params(), limits=LaneRiskLimits(vwap_revert_bp=0.0, trend_pause_bp=0.0),
        vwap60=100.0)
    assert "vwap_revert" not in (dec.skip or ""), dec.skip


# ═══════════════ [h359 2026-09-27] P2 v2：流驱动偏离回避 ═══════════════
# h358：OFI 推离 VWAP 时回归侧 f30 −0.79(t=−23.9)；OFI 回归时 +0.79(t=16.1)。
# vwap_flow_block>0 时：偏离≥vwap_revert_bp 且 OFI 推离 ⇒ 回归侧也封（不 fade 流）。


def _limits_v2():
    return LaneRiskLimits(vwap_revert_bp=2.0, vwap_flow_block=0.3, trend_pause_bp=0.0)


def test_v2_flat_above_vwap_buy_flow_blocks_both_sides():
    """空仓 + 价高于 VWAP + 买流推离 ⇒ 双侧封（回归卖也不 fade 流）。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=0.5,
        params=_params(), limits=_limits_v2(), vwap60=100.0)
    assert dec.bid == 0 and dec.ask == 0, f"流驱动偏离应双侧封，skip={dec.skip}"
    # 双侧封时聚合器取 why_buy（vwap_revert_up）——v2 语义由 ask==0 与
    # test_v2_off_v1_semantics_only（v2 关时 ask>0）对照捕获
    assert "vwap" in (dec.skip or ""), dec.skip


def test_v2_flat_above_vwap_flow_reverting_sell_allowed():
    """空仓 + 价高于 VWAP + 卖流已回归 ⇒ 回归卖放行、买仍封。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.5,
        params=_params(), limits=_limits_v2(), vwap60=100.0)
    assert dec.bid == 0, "偏离上方买仍封（回归=向下）"
    assert dec.ask > 0, "流已回归 ⇒ 回归侧卖应放行"
    assert "vwap_flow_away" not in (dec.skip or ""), dec.skip


def test_v2_long_above_vwap_buy_flow_reduce_sell_exempt():
    """多头 + 价高于 VWAP + 买流推离 ⇒ 卖=减仓侧豁免放行、买封。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=0.5,
        params=_params(), limits=_limits_v2(), book=_mk_book(qty=0.5), vwap60=100.0)
    assert dec.ask > 0, "多头减仓卖必须放行（F76 核心）"
    assert dec.bid == 0, "多头加仓买被封"
    assert "vwap_flow_away" not in (dec.skip or ""), dec.skip


def test_v2_flat_below_vwap_sell_flow_blocks_both_sides():
    """空仓 + 价低于 VWAP + 卖流推离 ⇒ 双侧封。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=98.0, seg_low=97.9, seg_high=98.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.5,
        params=_params(), limits=_limits_v2(), vwap60=100.0)
    assert dec.bid == 0 and dec.ask == 0, f"流驱动偏离应双侧封，skip={dec.skip}"
    assert "vwap" in (dec.skip or ""), dec.skip


def test_v2_off_v1_semantics_only():
    """vwap_flow_block=0 ⇒ 仅 v1 行为：回归卖照挂（不 fade 流确认）。"""
    dec, _ = mmrunner.plan_tick(
        state=_mk_state(), mid=102.0, seg_low=101.9, seg_high=102.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=0.9,
        params=_params(),
        limits=LaneRiskLimits(vwap_revert_bp=2.0, vwap_flow_block=0.0, trend_pause_bp=0.0),
        vwap60=100.0)
    assert dec.ask > 0, "v2 关闭时回归侧卖放行"
    assert "vwap_flow_away" not in (dec.skip or ""), dec.skip
