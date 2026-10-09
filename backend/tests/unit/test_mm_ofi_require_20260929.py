# -*- coding: utf-8 -*-
"""[R201 2026-09-29] **饱和要求闸**（`ofi_require_threshold`）契约测试。

它要守的四件事：
  1. **默认关闭时逐字不变**（θ=0 ⇒ 与改动前完全一致）——这是能回退的前提 ✓；
  2. 语义是**要求**：`|ofi| < θ` ⇒ **不建仓**（空仓时两侧都封）；
     与既有两个 OFI 闸（`|ofi| > θ` ⇒ 封逆势侧 ⇒ 调高=放松）**方向相反** ✓；
  3. **减仓侧永不受限**（F76：封减仓 ⇒ 库存只能等超时 taker 平 ⇒ 历史亏损主因 ✗）；
  4. **探针不被遮蔽**：被更早的趋势闸抢先时，`GATE_PROBES` 仍要计数 ✓
     （R201 的教训：`ofi_confirm_threshold` 因 `and allow_*` 守卫被抢先 ⇒ 0 次而"看着像没生效"）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as _mm  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    LaneRiskLimits,
    Position,
    QuoteParams,
)

_UP = [100.0 + 0.025 * i for i in range(21)]      # 20 期净移动 ≥15bp ⇒ 趋势闸会触发


def _mk_state(hist=None):
    st = _mm.SymbolState(symbol="BTC")
    st.mid_hist = list(hist if hist is not None else [100.0] * 21)
    return st


def _mk_book(qty=0.0):
    book = InventoryBook()
    if qty:
        book.positions["BTC"] = Position(
            qty=qty, avg_px=100.0, avg_mid=100.0, opened_ts=1_000_000.0,
            last_ts=1_000_000.0,
        )
    return book


def _limits(**kw):
    """只开被测闸，其余流向/趋势闸全关以隔离。"""
    base = dict(trend_pause_bp=0.0, ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
                ofi_require_threshold=0.0, pullback_flow_block=0.0, vwap_revert_bp=0.0,
                vwap_flow_block=0.0)
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(ofi, qty=0.0, hist=None, **limkw):
    _mm.GATE_PROBES.clear()
    dec, _ = _mm.plan_tick(
        state=_mk_state(hist), mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0, ofi=ofi,
        params=QuoteParams(), limits=_limits(**limkw), book=_mk_book(qty),
    )
    return dec, dict(_mm.GATE_PROBES)


# ── 1. 默认关闭 ⇒ 逐字不变 ────────────────────────────────────────────────────
def test_default_off_is_identical():
    for ofi in (0.0, 0.3, -0.3, 0.95):
        dec, probes = _tick(ofi)
        assert dec.bid > 0 and dec.ask > 0, f"θ=0 时两侧都应挂单（ofi={ofi} skip={dec.skip}）"
        assert not probes, f"θ=0 时探针不应计数：{probes}"


# ── 2. 要求语义：|ofi| < θ ⇒ 不建仓 ─────────────────────────────────────────
def test_weak_flow_blocks_both_sides_when_flat():
    dec, probes = _tick(0.3, ofi_require_threshold=0.9)
    assert dec.bid == 0 and dec.ask == 0, f"弱流下空仓两侧都不该挂：skip={dec.skip}"
    assert "ofi_require" in (dec.skip or ""), dec.skip
    assert probes.get("ofi_require_hit", 0) == 1 and probes.get("ofi_require_blocked", 0) == 1


def test_saturated_flow_allows_quoting():
    dec, _ = _tick(0.95, ofi_require_threshold=0.9)
    assert dec.bid > 0 and dec.ask > 0, f"饱和态应放行：skip={dec.skip}"


def test_threshold_boundary_is_strict_less_than():
    # |ofi| == θ 不算「小于」⇒ 放行（边界取 < 而非 ≤，与文档一致）
    dec, _ = _tick(0.9, ofi_require_threshold=0.9)
    assert dec.bid > 0 and dec.ask > 0, f"|ofi|=θ 应放行：skip={dec.skip}"


# ── 3. 减仓侧豁免（F76）─────────────────────────────────────────────────────
def test_long_position_blocks_buy_allows_reduce_sell():
    dec, _ = _tick(0.2, qty=1.0, ofi_require_threshold=0.9)
    assert dec.bid == 0, "多头在弱流下不该加仓（买）"
    assert dec.ask > 0, "多头减仓侧（卖）必须放行 ✗"


def test_short_position_blocks_sell_allows_reduce_buy():
    dec, _ = _tick(-0.2, qty=-1.0, ofi_require_threshold=0.9)
    assert dec.ask == 0, "空头在弱流下不该加仓（卖）"
    assert dec.bid > 0, "空头回补侧（买）必须放行 ✗"


# ── 4. 探针不被更早的闸遮蔽（R201 的核心教训）──────────────────────────────
def test_probe_counts_even_when_trend_gate_preempts():
    # 上涨趋势 ⇒ 趋势闸先封卖（trend_up）；我方闸仍须记到「命中」✓
    _dec, probes = _tick(0.2, hist=_UP, ofi_require_threshold=0.9, trend_pause_bp=15.0)
    assert probes.get("ofi_require_hit", 0) == 1, f"被抢先前置时探针仍须计数：{probes}"
