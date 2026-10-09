# -*- coding: utf-8 -*-
"""[F231 2026-09-15] 波动条件止损契约：止损只在 σ_norm ≥ stop_loss_vol_min 时生效。

证据（F230 四场景同窗口回放）：常数止损没有单一最优值——10bp 在阴跌/高波动
胜出，但在正常日多亏 ✗（震荡里被反复打止损、白付 taker 腿）。设计结论：
止损必须绑定 regime（波动高才开），`stop_loss_vol_min=0` = 恒启用 = 旧行为 ✓。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits,
    QuoteParams,
)
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402


def _run(sigma_norm: float, stop_bp: float, vol_min: float, mid: float = 99.85):
    """建一个浮亏 -15bp 的多头（avg_mid=100, mid=99.85），跑一个 tick。"""
    now = time.time()
    st = SymbolState(symbol="BTC", qty=1.0, avg_px=100.0, avg_mid=100.0,
                     opened_ts=now - 10.0)
    limits = LaneRiskLimits(stop_loss_bp=stop_bp, stop_loss_vol_min=vol_min,
                            max_one_side_seconds=3600.0, vol_pause_sigma=0.0,
                            trend_pause_bp=0.0,
                            min_hold_seconds=0.0)  # F258 后默认 30s，本测试持仓仅 10s
    dec, _ = plan_tick(
        state=st, mid=mid, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        params=QuoteParams(w_base_bp=4.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits, equity=300.0, fill_notional=30.0,
        taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=0.05,
        sigma_norm=sigma_norm, book=None, marks={"BTC": 100.0},
        ofi=0.0, day_pnl_usd=0.0,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)
    return st, dec


def test_vol_min_zero_is_always_on():
    """vol_min=0 ⇒ 恒启用（旧行为逐字一致）：σ 再低也止损。"""
    st, dec = _run(sigma_norm=0.2, stop_bp=10.0, vol_min=0.0)
    assert any(f.is_flatten for f in dec.fills), "vol_min=0 时必须止损"
    assert abs(st.qty) < 1e-9 and dec.skip == "stop_loss"


def test_below_vol_min_no_stop():
    """σ_norm < vol_min ⇒ 止损关闭：浮亏 -15bp 也不平（避免正常日被反复割 ✗）。"""
    st, dec = _run(sigma_norm=0.5, stop_bp=10.0, vol_min=1.0)
    assert not any(f.is_flatten for f in dec.fills), \
        "σ 低于阈值时不得打止损（这是 F231 的核心语义）"
    assert abs(st.qty - 1.0) < 1e-9, "仓位必须保持"


def test_above_vol_min_stops():
    """σ_norm ≥ vol_min ⇒ 止损启用：立即平仓。"""
    st, dec = _run(sigma_norm=1.5, stop_bp=10.0, vol_min=1.0)
    assert any(f.is_flatten for f in dec.fills), "σ 高于阈值时必须止损"
    assert abs(st.qty) < 1e-9 and dec.skip == "stop_loss"


def test_stop_bp_zero_never_stops():
    """stop_loss_bp=0 ⇒ 无论 σ 多高都不止损（旧行为）。"""
    st, dec = _run(sigma_norm=3.0, stop_bp=0.0, vol_min=1.0)
    assert not any(f.is_flatten for f in dec.fills)
    assert abs(st.qty - 1.0) < 1e-9
