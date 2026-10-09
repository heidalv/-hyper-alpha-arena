# -*- coding: utf-8 -*-
"""[h411 2026-09-27] 持仓硬上限（timeout_hard_taker_sec）回归测试。

规则：timeout_exit_maker_only=true 时，90s 超时只"停止加仓+挂减仓地板单"；
持仓年龄超过 timeout_hard_taker_sec（>0）⇒ 无条件 taker 平仓
（exit_path=timeout_hard_taker）。0=关闭=旧行为逐字。用户时域约定=300s。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _limits(**kw):
    base = dict(stop_loss_bp=0.0, take_profit_bp=0.0, stop_maker_grace_sec=0.0,
                min_hold_seconds=0.0, trend_pause_bp=0.0, sudden_move_bp=0.0,
                ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
                max_one_side_seconds=90.0, timeout_exit_maker_only=True)
    base.update(kw)
    return LaneRiskLimits(**base)


def _pos_state(age_sec):
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = 1_000_000.0
    st.last_ts = 1_000_000.0
    st.mid_hist = [100.0] * 20
    return st, 1_000_000.0 + age_sec


def _tick(st, now, limits):
    return mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits)


def test_hard_cap_zero_is_unchanged():
    """硬上限 0（关闭）：500s 持仓仍是 maker_only 登记（旧行为）。"""
    st, now = _pos_state(500.0)
    dec, _ = _tick(st, now, _limits(timeout_hard_taker_sec=0.0))
    assert not any(f.is_flatten for f in dec.fills), "0=关闭，不得 taker"
    assert "timeout_maker_only" in (dec.skip or ""), dec.skip


def test_hard_cap_fires_taker_at_300():
    """maker_only + 硬上限 300：400s 持仓 ⇒ 无条件 taker 平仓。"""
    st, now = _pos_state(400.0)
    dec, _ = _tick(st, now, _limits(timeout_hard_taker_sec=300.0))
    flats = [f for f in dec.fills if f.is_flatten]
    assert flats, "超硬上限必须 taker 平仓"
    assert dec.exit_path == "timeout_hard_taker", dec.exit_path
    assert abs(st.qty) < 1e-9, "仓位必须清零"


def test_below_hard_cap_stays_maker_only():
    """120s 持仓（>90s 超时、<300s 硬上限）⇒ 仍 maker_only，不 taker。"""
    st, now = _pos_state(120.0)
    dec, _ = _tick(st, now, _limits(timeout_hard_taker_sec=300.0))
    assert not any(f.is_flatten for f in dec.fills), "未到硬上限不得 taker"
    assert "timeout_maker_only" in (dec.skip or ""), dec.skip


def test_maker_only_false_unchanged_by_hard_cap():
    """maker_only=false：90s 即 taker（旧分支），硬上限不参与。"""
    st, now = _pos_state(400.0)
    dec, _ = _tick(st, now, _limits(timeout_hard_taker_sec=300.0,
                                    timeout_exit_maker_only=False))
    flats = [f for f in dec.fills if f.is_flatten]
    assert flats and dec.exit_path == "timeout_taker", dec.exit_path


# ── [h413 2026-09-27] maker_only 超时的"停止加仓"接线修复 ─────────────────

def test_timeout_blocks_add_side_long():
    """多头超时（maker_only）：加仓侧=买被封、减仓侧=卖仍挂（F296 设计终于接线）。"""
    st, now = _pos_state(120.0)          # qty=+0.5 多头，120s > 90s 超时
    dec, _ = _tick(st, now, _limits(timeout_hard_taker_sec=0.0))
    assert not any(f.is_flatten for f in dec.fills), "maker_only 不 taker"
    assert dec.bid == 0.0, "多头超时后加仓侧（买）必须撤单"
    assert dec.ask > 0.0, "减仓侧（卖）必须保留"


def test_timeout_blocks_add_side_short():
    """空头超时（maker_only）：加仓侧=卖被封、减仓侧=买仍挂。"""
    st, now = _pos_state(120.0)
    st.qty = -0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    dec, _ = _tick(st, now, _limits(timeout_hard_taker_sec=0.0))
    assert not any(f.is_flatten for f in dec.fills), "maker_only 不 taker"
    assert dec.ask == 0.0, "空头超时后加仓侧（卖）必须撤单"
    assert dec.bid > 0.0, "减仓侧（买）必须保留"
