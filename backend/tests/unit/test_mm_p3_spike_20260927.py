# -*- coding: utf-8 -*-
"""[h401 2026-09-27] P3 尖峰 fade with 闸（p3_spike_gate）回归测试。

语义（h361 条件模型，n=52,343）：|75s 移动|≥3bp（尖峰）且 OFI 与 fade 方向同向
（上尖峰+卖流 / 下尖峰+买流，|ofi|≥阈值）⇒ 封追尖峰侧（上尖峰封买、下尖峰封卖）；
减仓侧豁免（F76）。with f30 +0.857(t=10.2)；against f30 −0.972(t=−18.2)。
live 映射：trend_move_bp(mid_hist, 5) ≈ 75s 移动。
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


def _limits(gate=0.3):
    return LaneRiskLimits(p3_spike_gate=gate, trend_pause_bp=0.0,
                          ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
                          stop_loss_bp=0.0)


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


# 上尖峰：最近 5 期 +4bp（75s 口径）；下尖峰：−4bp
_UP_SPIKE = [100.0] * 16 + [100.0 + 0.008 * i for i in range(6)]
_DOWN_SPIKE = [100.0] * 16 + [100.0 - 0.008 * i for i in range(6)]


def _tick(st, mid, ofi, limits, qty=0.0):
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=ofi,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits, book=_mk_book(qty=qty), marks={"BTC": mid},
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)


def test_up_spike_sell_flow_blocks_buy():
    """上尖峰 + 卖流（fade 同向）⇒ 封买（追涨侧）；卖放行。"""
    st = _mk_state(_UP_SPIKE)
    dec, _ = _tick(st, 100.04, -0.5, _limits())
    assert dec.bid == 0, f"应封买，skip={dec.skip}"
    assert "p3_spike_fade_buy" in (dec.skip or ""), dec.skip
    assert dec.ask > 0, "卖侧应放行"


def test_down_spike_buy_flow_blocks_sell():
    """下尖峰 + 买流（fade 同向）⇒ 封卖（追跌侧）；买放行。"""
    st = _mk_state(_DOWN_SPIKE)
    dec, _ = _tick(st, 99.96, 0.5, _limits())
    assert dec.ask == 0, f"应封卖，skip={dec.skip}"
    assert "p3_spike_fade_sell" in (dec.skip or ""), dec.skip
    assert dec.bid > 0, "买侧应放行"


def test_spike_with_follow_flow_no_block():
    """上尖峰 + 买流（追涨流，非 fade）⇒ 不封（该侧不是 against 腿）。"""
    st = _mk_state(_UP_SPIKE)
    dec, _ = _tick(st, 100.04, 0.5, _limits())
    assert "p3_spike" not in (dec.skip or ""), dec.skip


def test_below_3bp_no_block():
    """|75s 移动| < 3bp ⇒ 不触发。"""
    hist = [100.0] * 16 + [100.0 + 0.004 * i for i in range(6)]  # +2bp
    st = _mk_state(hist)
    dec, _ = _tick(st, 100.02, -0.9, _limits())
    assert "p3_spike" not in (dec.skip or ""), dec.skip


def test_gate_off_no_block():
    """p3_spike_gate=0 ⇒ 完全不动作（回滚态）。"""
    st = _mk_state(_UP_SPIKE)
    dec, _ = _tick(st, 100.04, -0.9, _limits(gate=0.0))
    assert "p3_spike" not in (dec.skip or ""), dec.skip


def test_reduce_side_exempt():
    """空头 + 上尖峰 + 卖流 ⇒ 封买？空头的买=减仓侧 ⇒ 豁免放行。"""
    st = _mk_state(_UP_SPIKE)
    dec, _ = _tick(st, 100.04, -0.5, _limits(), qty=-0.5)
    assert dec.bid > 0, "空头回补买应放行"
