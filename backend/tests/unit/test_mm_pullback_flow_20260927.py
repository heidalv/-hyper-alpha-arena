# -*- coding: utf-8 -*-
"""[h356 2026-09-27] P1 薄流确认闸（pullback_flow_block）回归测试。

语义（h355 条件模型，n=12,925）：|300s 趋势|≥15bp（P1 触发区）且 OFI 逆势流动
（|ofi|≥阈值）⇒ 封趋势同向加仓侧（= 放弃流驱动回调）；减仓侧豁免（F76）。
300s 趋势独立计算，不受 side_mode=model 的 60s 模型方向影响。
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
            last_ts=1_000_000.0,
        )
    return book


def _limits():
    """只开 P1 薄流闸（0.3），其余流向/趋势闸关闭以隔离。"""
    return LaneRiskLimits(pullback_flow_block=0.3, trend_pause_bp=0.0,
                          ofi_confirm_threshold=0.0, ofi_block_threshold=0.0)


# 上涨趋势 +50bp / 下跌趋势 −50bp（20 期净移动，≥15bp = P1 触发区）
_UP = [100.0 + 0.025 * i for i in range(21)]
_DOWN = [100.0 - 0.025 * i for i in range(21)]


def test_flat_uptrend_sell_flow_blocks_buy():
    """空仓 + 涨势 + 卖流在压（ofi=−0.5）= 流驱动回调 ⇒ 封买、卖放行。"""
    st = _mk_state(_UP)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.5,
        params=QuoteParams(), limits=_limits(),
    )
    assert dec.bid == 0, f"流驱动回调应封买，skip={dec.skip}"
    assert "pullback_flow_sell" in (dec.skip or ""), dec.skip
    assert dec.ask > 0, "卖侧应放行（trend_pause 已关）"


def test_flat_downtrend_buy_flow_blocks_sell():
    """空仓 + 跌势 + 买流在推（ofi=+0.5）= 流驱动反弹 ⇒ 封卖、买放行。"""
    st = _mk_state(_DOWN)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=99.5, seg_low=99.4, seg_high=99.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=0.5,
        params=QuoteParams(), limits=_limits(),
    )
    assert dec.ask == 0, f"流驱动反弹应封卖，skip={dec.skip}"
    assert "pullback_flow_buy" in (dec.skip or ""), dec.skip
    assert dec.bid > 0, "买侧应放行（trend_pause 已关）"


def test_long_uptrend_sell_flow_buy_blocked_reduce_sell_ok():
    """多头 + 涨势 + 卖流 ⇒ 买=加仓侧被封、卖=减仓侧豁免放行（F76）。"""
    st = _mk_state(_UP)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.5,
        params=QuoteParams(), limits=_limits(), book=_mk_book(qty=0.5),
    )
    assert dec.bid == 0, "多头加仓买应被封"
    assert dec.ask > 0, "多头减仓卖必须放行（F76 核心）"


def test_short_uptrend_sell_flow_buy_reduce_exempt():
    """空头 + 涨势 + 卖流 ⇒ 买=回补侧豁免放行。"""
    st = _mk_state(_UP)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.5,
        params=QuoteParams(), limits=_limits(), book=_mk_book(qty=-0.5),
    )
    assert dec.bid > 0, f"空头回补买应放行，skip={dec.skip}"


def test_weak_trend_no_block():
    """|趋势| < 15bp（P1 触发区之外）⇒ 闸不动作，双侧放行。"""
    st = _mk_state([100.0 + 0.005 * i for i in range(21)])  # +10bp
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.1, seg_low=100.0, seg_high=100.2,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.9,
        params=QuoteParams(), limits=_limits(),
    )
    assert "pullback_flow" not in (dec.skip or ""), dec.skip


def test_gate_off_no_block():
    """pullback_flow_block=0 ⇒ 完全不动作（回滚态）。"""
    st = _mk_state(_UP)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.5, seg_low=100.4, seg_high=100.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.9,
        params=QuoteParams(), limits=LaneRiskLimits(pullback_flow_block=0.0,
                                                    trend_pause_bp=0.0),
    )
    assert "pullback_flow" not in (dec.skip or ""), dec.skip


# ── [h400 2026-09-27] P1 触发区下限参数化 ────────────────────────────────
# 注：trend_move_bp(20 期) 只看 19 步净移动 ⇒ 0.0055×19 ≈ +10.4bp（≥10 且 <15）
_WEAK10 = [100.0 + 0.0055 * i for i in range(21)]


def test_p1_trigger_10_blocks_weak_trend():
    """p1_trigger_bp=10：≈10.4bp 趋势（默认 15 之外）也进入 P1 触发区 ⇒ 封买。"""
    st = _mk_state(_WEAK10)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.11, seg_low=100.0, seg_high=100.2,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.9,
        params=QuoteParams(),
        limits=LaneRiskLimits(pullback_flow_block=0.3, trend_pause_bp=0.0,
                              p1_trigger_bp=10.0),
    )
    assert "pullback_flow_sell" in (dec.skip or ""), dec.skip
    assert dec.bid == 0


def test_p1_trigger_default_15_ignores_10bp():
    """默认 15：≈10.4bp 趋势不触发（旧行为）。"""
    st = _mk_state(_WEAK10)
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.11, seg_low=100.0, seg_high=100.2,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=-0.9,
        params=QuoteParams(), limits=_limits(),
    )
    assert "pullback_flow" not in (dec.skip or ""), dec.skip
