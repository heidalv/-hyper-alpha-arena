# -*- coding: utf-8 -*-
"""[F264 2026-09-16] 快武装止损契约：
  ① stop_loss_fast_mult>0 且本步 |中价移动| ≥ mult×vol_baseline 且浮亏 ≥ stop_loss_bp
     ⇒ 跳过慢速 σ_norm 波动闸直接平仓（快跌早武装，F250#4）；
  ② 步长不足 ⇒ 不武装（旧逻辑照旧：σ 低则止损关闭）；
  ③ mult=0 ⇒ 旧行为逐字一致（σ 低不武装）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402


def _limits(stop_bp: float = 10.0, vol_min: float = 1.0, fast_mult: float = 5.0):
    return LaneRiskLimits(stop_loss_bp=stop_bp, stop_loss_vol_min=vol_min,
                          stop_loss_fast_mult=fast_mult, stop_maker_grace_sec=0.0,
                          max_one_side_seconds=3600.0, vol_pause_sigma=0.0,
                          trend_pause_bp=0.0,
                          min_hold_seconds=0.0)  # F258 后默认 30s，本测试持仓仅 10s


def _tick(mid: float, limits, sigma_norm: float = 0.2):
    st = SymbolState(symbol="XRP", qty=1.0, avg_px=100.0, avg_mid=100.0,
                     opened_ts=time.time() - 10.0)
    # 与 live/回放同构：调用方在 plan_tick **之前**把本 tick 的 mid 追加进 mid_hist
    # ⇒ mid_hist[-1] == 当前 mid，上一快照是 [-2]。
    st.mid_hist = [100.0] * 19 + [mid]
    st.vol_baseline_bp = 2.0
    dec, _ = plan_tick(
        state=st, mid=mid, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=time.time(),
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits, equity=300.0, fill_notional=30.0,
        taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=0.05,
        sigma_norm=sigma_norm, book=None, marks={"XRP": mid},
        ofi=0.0, day_pnl_usd=0.0,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)
    return st, dec


def test_fast_arm_flattens_despite_low_sigma():
    """σ=0.2 < vol_min=1.0（慢闸不武装），但本步 −300bp ≥ 5×2bp ⇒ 快武装平仓。"""
    st, dec = _tick(mid=97.0, limits=_limits())
    assert any(f.is_flatten for f in dec.fills), "快武装必须平仓"
    assert dec.skip == "stop_loss"
    assert abs(st.qty) < 1e-9


def test_no_fast_arm_when_step_small():
    """本步 −0.05bp < 5×2bp ⇒ 不武装；σ 低 ⇒ 止损关闭 ⇒ 不平仓（旧行为）。"""
    st, dec = _tick(mid=99.9995, limits=_limits())
    assert not any(f.is_flatten for f in dec.fills)
    assert abs(st.qty - 1.0) < 1e-9


def test_fast_mult_zero_is_old_behavior():
    """mult=0 关闭快武装：即使大踏步 + 浮亏，σ 低时也绝不武装（旧行为逐字一致）。"""
    st, dec = _tick(mid=97.0, limits=_limits(fast_mult=0.0))
    assert not any(f.is_flatten for f in dec.fills)
    assert abs(st.qty - 1.0) < 1e-9
