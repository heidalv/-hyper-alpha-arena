# -*- coding: utf-8 -*-
"""[F217 2026-09-15] `side_mode="counter_trend"` 的方向性契约测试。

依据（修复后模型/账本实测）：被动入场腿的逆向选择 ≈ −0.84bp 是全部剩余亏损 ✗；
买腿 30 分钟 markout +1.90 ✓ / 卖腿 −4.51 ✗（F198b）⇒ "只挂逆势侧"是唯一能改
入场符号的旋钮。方向必须钉死：**涨 ⇒ 只挂卖（别买）、跌 ⇒ 只挂买（别卖）** ✓
（与 `k_trend` 偏斜同一方向、与 F204 修正后的趋势闸同一方向 —— 三处必须一致 ✓）。
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
    QuoteParams,
)

_UP = [100.0 + 0.05 * i for i in range(40)]      # 净上涨
_DOWN = [100.0 - 0.05 * i for i in range(40)]    # 净下跌


def _tick(hist, mid, **kw):
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = list(hist)
    p = QuoteParams(side_mode="counter_trend", w_base_bp=10.0, k_vol=0.0, k_inv=0.0,
                    frozen_width_bp=None, **kw)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=mid, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=p, limits=LaneRiskLimits(), book=InventoryBook(),
    )
    return dec


def test_default_both_is_unchanged():
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = list(_UP)
    p = QuoteParams(w_base_bp=10.0, k_vol=0.0, k_inv=0.0, frozen_width_bp=None)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, params=p, limits=LaneRiskLimits(),
        book=InventoryBook())
    assert dec.bid > 0 and dec.ask > 0, "默认 both ⇒ 双边都挂"


def test_uprise_keeps_only_the_ask():
    """**方向性命门**：涨 ⇒ 只挂卖（卖 rip ✓）、买被禁 ✗。"""
    dec = _tick(_UP, 102.0)
    assert dec.ask > 0, "上涨时卖侧必须在"
    assert dec.bid == 0, "上涨时买侧必须被禁（别追买 ✗）"
    assert "ct_trend_up" in (dec.skip or ""), dec.skip


def test_downmove_keeps_only_the_bid():
    """跌 ⇒ 只挂买（买 dip ✓）、卖被禁 ✗。"""
    dec = _tick(_DOWN, 98.0)
    assert dec.bid > 0, "下跌时买侧必须在"
    assert dec.ask == 0, "下跌时卖侧必须被禁（别杀跌 ✗）"
    assert "ct_trend_down" in (dec.skip or ""), dec.skip


def test_below_threshold_is_two_sided():
    """|趋势| ≤ side_trend_min_bp ⇒ 双侧都挂（防噪声切单边）。"""
    dec = _tick([100.0, 100.001, 100.0], 100.0, side_trend_min_bp=5.0)
    assert dec.bid > 0 and dec.ask > 0, "小趋势下应双边挂"
