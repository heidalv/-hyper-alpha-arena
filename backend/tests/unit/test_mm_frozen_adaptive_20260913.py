# -*- coding: utf-8 -*-
"""[2026-09-13 F80] 冻结自适应挂宽契约（默认关闭 = 旧行为逐字一致）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    QuoteParams,
    compute_quote,
)


def test_frozen_off_by_default_unchanged_behavior():
    """frozen_width_bp=None ⇒ 挂宽与旧版逐字一致（不受 slow_range 影响）。"""
    p = QuoteParams(w_base_bp=7.0, k_inv=1.0)
    q1 = compute_quote(symbol="BTC", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                       slow_range_bp=2.0, params=p)
    q2 = compute_quote(symbol="BTC", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                       slow_range_bp=20.0, params=p)
    assert q1.w_bid_bp == pytest.approx(7.0) and q2.w_bid_bp == pytest.approx(7.0)


def test_frozen_triggers_narrow_width():
    """冻结信号（单步最大移动 < 阈值）⇒ 挂宽 = frozen_width_bp。"""
    p = QuoteParams(w_base_bp=7.0, k_inv=1.0, frozen_width_bp=3.0,
                    frozen_max_move_bp=5.0)
    q = compute_quote(symbol="BTC", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                      slow_range_bp=2.5, params=p)
    assert q.w_bid_bp == pytest.approx(3.0)
    assert q.w_ask_bp == pytest.approx(3.0)


def test_frozen_above_threshold_uses_normal_width():
    """单步最大移动 ≥ 阈值 ⇒ 回到正常挂宽（含波动缩放）。"""
    p = QuoteParams(w_base_bp=7.0, k_inv=1.0, frozen_width_bp=3.0,
                    frozen_max_move_bp=5.0)
    q = compute_quote(symbol="BTC", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                      slow_range_bp=7.0, params=p)
    assert q.w_bid_bp == pytest.approx(7.0)


def test_plan_tick_computes_slow_move_and_passes():
    """plan_tick 必须计算单步最大移动并传给 compute_quote（否则冻结永不触发）。"""
    import inspect
    src = inspect.getsource(mmrunner.plan_tick)
    assert "slow_move_bp" in src
    assert "slow_range_bp=slow_move_bp" in src
    assert "frozen_lookback" in src


def test_plan_tick_frozen_mode_narrows_quotes_behavioral():
    """行为级：平静 mid_hist + 冻结参数 ⇒ 双侧报价宽度 ≈ 3bp。"""
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = [100.0 + 0.001 * i for i in range(80)]   # 每期仅 0.1bp 移动
    from backend.services.market_maker.core import LaneRiskLimits
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.08, seg_low=100.07, seg_high=100.09,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1_000_030.0,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(w_base_bp=7.0, k_inv=1.0, frozen_width_bp=3.0,
                           frozen_max_move_bp=5.0, frozen_lookback=60),
        limits=LaneRiskLimits(trend_pause_bp=0.0, vol_pause_mult=0.0),
    )
    assert dec.bid > 0 and dec.ask > 0
    w_bid = (100.08 - dec.bid) / 100.08 * 1e4
    w_ask = (dec.ask - 100.08) / 100.08 * 1e4
    assert w_bid == pytest.approx(3.0, abs=0.3), w_bid
    assert w_ask == pytest.approx(3.0, abs=0.3), w_ask
