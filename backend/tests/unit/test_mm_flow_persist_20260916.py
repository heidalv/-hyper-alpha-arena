# -*- coding: utf-8 -*-
"""[F274 · E2'] 持续性单边流闸契约：
  连续 N 桶同向主动流（|OFI| ≥ floor）⇒ 该币**站开不挂**（两侧都撤）；
  未达 N ⇒ 不干预；N=0 ⇒ 关闭（旧行为逐字一致）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402


def _limits(n: int):
    return LaneRiskLimits(flow_persist_pause=n, flow_persist_floor=0.2,
                          ofi_block_threshold=0.0, vol_pause_sigma=0.0,
                          trend_pause_bp=0.0, stop_loss_bp=0.0,
                          max_one_side_seconds=3600.0,
                          max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
                          max_gross_notional_ratio=1.0)


def _tick(streak: float, limits):
    st = SymbolState(symbol="BTC")
    st.mid_hist = [100.0] * 20
    st.vol_baseline_bp = 2.0
    dec, _ = plan_tick(
        state=st, mid=100.0, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=time.time(),
        params=QuoteParams(w_base_bp=10.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits, equity=300.0, fill_notional=30.0,
        taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=0.05,
        sigma_norm=0.0, book=None, marks={"BTC": 100.0},
        ofi=0.0, day_pnl_usd=0.0, flow_streak=streak,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)
    return st, dec


def test_persist_streak_pauses_both_sides():
    st, dec = _tick(streak=5.0, limits=_limits(5))
    assert dec.action == "pause" and dec.skip == "flow_persist", (dec.action, dec.skip)
    assert dec.bid == 0.0 and dec.ask == 0.0
    assert st.quote_bid == 0.0 and st.quote_ask == 0.0, "站开必须撤掉旧挂单"


def test_below_threshold_quotes_normally():
    st, dec = _tick(streak=3.0, limits=_limits(5))
    assert dec.action != "pause"
    assert dec.bid > 0.0 and dec.ask > 0.0


def test_disabled_by_default():
    st, dec = _tick(streak=50.0, limits=_limits(0))
    assert dec.action != "pause" and dec.bid > 0.0 and dec.ask > 0.0


def test_negative_streak_also_pauses():
    """卖压持续同样站开（对称）。"""
    st, dec = _tick(streak=-6.0, limits=_limits(5))
    assert dec.action == "pause" and dec.skip == "flow_persist"
