# -*- coding: utf-8 -*-
"""[h405 2026-09-27] #12 分形态×分币种启停表（pattern_matrix）回归测试。

语义：lane meta.pattern_matrix = {"BTC": ["P45"]} ⇒ BTC 空仓时只在 P4/P5 形态
上下文挂单；其它上下文 pause（skip=pattern_matrix(...)）。有持仓豁免（减仓侧
必须存活 F76）。None/空 = 旧行为逐字。
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


def _limits():
    return LaneRiskLimits(stop_loss_bp=0.0, take_profit_bp=0.0,
                          stop_maker_grace_sec=0.0, min_hold_seconds=0.0,
                          trend_pause_bp=0.0, ofi_confirm_threshold=0.0,
                          ofi_block_threshold=0.0, max_one_side_seconds=3600.0)


def _tick(st, mid, matrix=None, qty=0.0):
    book = InventoryBook()
    if qty:
        book.positions["BTC"] = Position(qty=qty, avg_px=100.0, avg_mid=100.0,
                                         opened_ts=1_000_000.0, last_ts=1_000_000.0)
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=_limits(), book=book, marks={"BTC": mid},
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        pattern_matrix=matrix)


def _st_with_hist(hist):
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = list(hist)
    return st


_P1_HIST = [100.0 + 1.0 * i for i in range(21)]        # +20bp ⇒ P1
_P45_HIST = [100.0, 100.4, 100.1, 100.5, 100.2, 100.7, 100.3, 100.7, 100.7]


def test_matrix_blocks_non_allowed_context():
    """BTC 只允许 P45：P1 上下文 ⇒ pause。"""
    st = _st_with_hist(_P1_HIST)
    dec, _ = _tick(st, 100.2, matrix={"BTC": ["P45"]})
    assert dec.action == "pause" and "pattern_matrix" in (dec.skip or ""), dec.skip


def test_matrix_allows_matching_context():
    """BTC 只允许 P45：P45 上下文 ⇒ 正常挂单。"""
    st = _st_with_hist(_P45_HIST)
    dec, _ = _tick(st, 100.7, matrix={"BTC": ["P45"]})
    assert "pattern_matrix" not in (dec.skip or ""), dec.skip
    assert dec.bid > 0 or dec.ask > 0, "应正常挂单"


def test_matrix_exempt_with_position():
    """有持仓 ⇒ 豁免（减仓侧必须存活 F76）。"""
    st = _st_with_hist(_P1_HIST)
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = 1_000_000.0
    dec, _ = _tick(st, 100.2, matrix={"BTC": ["P45"]}, qty=0.5)
    assert "pattern_matrix" not in (dec.skip or ""), dec.skip


def test_matrix_other_symbol_untouched():
    """矩阵只作用于列出的币。"""
    st = mmrunner.SymbolState(symbol="ETH")
    st.mid_hist = list(_P1_HIST)
    dec, _ = _tick(st, 100.2, matrix={"BTC": ["P45"]})
    assert "pattern_matrix" not in (dec.skip or ""), dec.skip


def test_matrix_none_is_unchanged():
    """None/空矩阵 ⇒ 旧行为。"""
    st = _st_with_hist(_P1_HIST)
    dec, _ = _tick(st, 100.2, matrix=None)
    assert "pattern_matrix" not in (dec.skip or ""), dec.skip
    dec2, _ = _tick(st, 100.2, matrix={})
    assert "pattern_matrix" not in (dec2.skip or ""), dec2.skip


def test_tick_wiring_source_contract():
    """接线契约：tick 必须把 lane meta.pattern_matrix 传给 plan_tick。"""
    import inspect

    from backend.services.market_maker.runner import ShadowRunner
    src = inspect.getsource(ShadowRunner.tick)
    assert 'pattern_matrix=(self.meta or {}).get("pattern_matrix")' in src, \
        "tick 未传 pattern_matrix（死闸防护，同 F272/h397 教训）"
