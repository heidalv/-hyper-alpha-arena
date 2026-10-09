# -*- coding: utf-8 -*-
"""[h363 2026-09-27] P4 双触突破 with 闸 / P5 挤压突破 with 闸 回归测试。

语义（h361, n=138,569）：
  P4：120s 窗口两次触及极值且现价贴极值 + OFI 与突破方向同向（|ofi|≥闸值）
      ⇒ 封逆突破侧（只挂突破方向），减仓侧豁免（F76）。
  P5：60s 波动 < 滚动 30 分位 且 |r15|≥2bp + OFI 与动量方向同向 ⇒ 封逆动量侧。
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


def _limits(p4=0.0, p5=0.0):
    return LaneRiskLimits(p4_breakout_gate=p4, p5_squeeze_gate=p5,
                          trend_pause_bp=0.0, ofi_confirm_threshold=0.0,
                          ofi_block_threshold=0.0)


def _tick(st, mid, ofi, limits, book=None):
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=ofi,
        params=QuoteParams(), limits=limits, book=book,
    )


# P4：120s 窗口（9 期）两次触高、现价贴高
_P4_UP = [100.0, 100.3, 101.0, 100.5, 101.0, 100.6, 100.4, 100.8, 101.0]
_P4_DOWN = [101.0, 100.7, 100.0, 100.5, 100.0, 100.4, 100.6, 100.2, 100.0]
# P5：交替 ±1bp 噪声 56 期（5 期窗 vol=4bp，q30=4bp）+ 平尾 +3bp（vol_now=3bp<4）
_P5_BASE = [100.0 + 0.01 * (k % 2) for k in range(56)]
_P5_UP = _P5_BASE + [100.01, 100.01, 100.01, 100.04]
_P5_DOWN = _P5_BASE + [100.01, 100.01, 100.01, 99.98]


def test_p4_up_breakout_flow_with_blocks_sell():
    """空仓 + 向上双触突破 + 买流 ⇒ 封卖、买放行。"""
    st = _mk_state(_P4_UP)
    dec, _ = _tick(st, 101.0, ofi=0.5, limits=_limits(p4=0.3))
    assert dec.ask == 0, f"逆突破侧卖应被封，skip={dec.skip}"
    assert "p4_breakout_buy" in (dec.skip or ""), dec.skip
    assert dec.bid > 0, "突破方向买应放行"


def test_p4_down_breakout_flow_with_blocks_buy():
    """空仓 + 向下双触突破 + 卖流 ⇒ 封买、卖放行。"""
    st = _mk_state(_P4_DOWN)
    dec, _ = _tick(st, 100.0, ofi=-0.5, limits=_limits(p4=0.3))
    assert dec.bid == 0, f"逆突破侧买应被封，skip={dec.skip}"
    assert "p4_breakout_sell" in (dec.skip or ""), dec.skip
    assert dec.ask > 0, "突破方向卖应放行"


def test_p4_long_reduce_exempt():
    """多头 + 向上突破 + 买流 ⇒ 卖=减仓侧豁免放行。"""
    st = _mk_state(_P4_UP)
    dec, _ = _tick(st, 101.0, ofi=0.5, limits=_limits(p4=0.3), book=_mk_book(qty=0.5))
    assert dec.ask > 0, "多头减仓卖必须放行（F76）"
    assert "p4_breakout" not in (dec.skip or ""), dec.skip


def test_p4_flow_against_no_block():
    """突破方向与 OFI 相反 ⇒ 闸不动作（双侧不受 P4 影响）。"""
    st = _mk_state(_P4_UP)
    dec, _ = _tick(st, 101.0, ofi=-0.5, limits=_limits(p4=0.3))
    assert "p4_breakout" not in (dec.skip or ""), dec.skip


def test_p4_gate_off():
    """p4_breakout_gate=0 ⇒ 不动作（回滚态）。"""
    st = _mk_state(_P4_UP)
    dec, _ = _tick(st, 101.0, ofi=0.9, limits=_limits(p4=0.0))
    assert "p4_breakout" not in (dec.skip or ""), dec.skip


def test_p5_up_squeeze_flow_with_blocks_sell():
    """空仓 + 挤压突破向上 + 买流 ⇒ 封卖、买放行。"""
    st = _mk_state(_P5_UP)
    dec, _ = _tick(st, 100.04, ofi=0.5, limits=_limits(p5=0.3))
    assert dec.ask == 0, f"逆动量侧卖应被封，skip={dec.skip}"
    assert "p5_squeeze_buy" in (dec.skip or ""), dec.skip
    assert dec.bid > 0, "动量方向买应放行"


def test_p5_down_squeeze_flow_with_blocks_buy():
    """空仓 + 挤压突破向下 + 卖流 ⇒ 封买、卖放行。"""
    st = _mk_state(_P5_DOWN)
    dec, _ = _tick(st, 99.98, ofi=-0.5, limits=_limits(p5=0.3))
    assert dec.bid == 0, f"逆动量侧买应被封，skip={dec.skip}"
    assert "p5_squeeze_sell" in (dec.skip or ""), dec.skip
    assert dec.ask > 0, "动量方向卖应放行"


def test_p5_short_reduce_exempt():
    """空头 + 向下动量 + 卖流 ⇒ 买=回补侧豁免放行。"""
    st = _mk_state(_P5_DOWN)
    dec, _ = _tick(st, 99.98, ofi=-0.5, limits=_limits(p5=0.3), book=_mk_book(qty=-0.5))
    assert dec.bid > 0, "空头回补买必须放行（F76）"
    assert "p5_squeeze" not in (dec.skip or ""), dec.skip


def test_p5_flow_against_no_block():
    """动量方向与 OFI 相反 ⇒ 闸不动作。"""
    st = _mk_state(_P5_UP)
    dec, _ = _tick(st, 100.04, ofi=-0.5, limits=_limits(p5=0.3))
    assert "p5_squeeze" not in (dec.skip or ""), dec.skip


def test_p5_gate_off():
    """p5_squeeze_gate=0 ⇒ 不动作（回滚态）。"""
    st = _mk_state(_P5_UP)
    dec, _ = _tick(st, 100.04, ofi=0.9, limits=_limits(p5=0.0))
    assert "p5_squeeze" not in (dec.skip or ""), dec.skip
