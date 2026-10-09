# -*- coding: utf-8 -*-
"""[h389 2026-09-27] 尾随锁利（trail_lock_bp）回归测试。

规则（与 h388 影子测验同公式）：MFE ≥ trail_lock_bp(20) ⇒ 止损线 = −5 +
10×⌊(MFE−20)/10⌋（20~29⇒−5、30~39⇒+5 …），浮盈回落至该线即触发，复用止损的
maker 宽限→taker 流程，exit_path=trail_lock_taker；平仓/换仓重置 MFE。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _state(qty=0.5, avg=100.0):
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = qty
    st.avg_px = avg
    st.avg_mid = avg
    st.opened_ts = 1_000_000.0
    st.last_ts = 1_000_000.0
    return st


def _limits(trail=0.0):
    return LaneRiskLimits(
        trail_lock_bp=trail, stop_loss_bp=40.0, take_profit_bp=0.0,
        stop_maker_grace_sec=0.0, min_hold_seconds=0.0,
        reversal_decay_bp=0.0, trend_pause_bp=0.0, sudden_move_bp=0.0,
        ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
    )


def _tick(st, mid, limits, now=1_000_030.0):
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=limits,
    )


def test_trail_raises_stop_then_locks_profit():
    """+30 ⇒ 止损线抬至 +5；浮盈回落 ≤+5 时以 trail_lock_taker 离场。"""
    st = _state()
    limits = _limits(trail=20.0)
    d1, _ = _tick(st, 100.30, limits)          # MFE=+30 ⇒ 线=+5
    assert not any(f.is_flatten for f in d1.fills), f"浮盈 +30 不应离场，{d1.skip}"
    assert abs(st.mfe_bp - 30.0) < 0.1, f"MFE 应为 ~30，实际 {st.mfe_bp}"
    d2, _ = _tick(st, 100.08, limits, now=1_000_045.0)   # +8 > +5 ⇒ 不离场
    assert not any(f.is_flatten for f in d2.fills), f"+8 高于尾随线，{d2.skip}"
    d3, _ = _tick(st, 100.03, limits, now=1_000_060.0)   # +3 ≤ +5 ⇒ 锁利离场
    assert any(f.is_flatten for f in d3.fills), f"回落至尾随线应离场，{d3.skip}"
    assert d3.exit_path == "trail_lock_taker", d3.exit_path
    assert st.qty == 0 and st.mfe_bp == 0.0, "平仓后应重置 MFE/仓位"


def test_trail_off_no_early_exit():
    """trail_lock_bp=0：+3 浮盈不触发任何离场（硬止损在 −40）。"""
    st = _state()
    limits = _limits(trail=0.0)
    _tick(st, 100.30, limits)
    d, _ = _tick(st, 100.03, limits, now=1_000_045.0)
    assert not any(f.is_flatten for f in d.fills), f"尾随关闭时 +3 不应离场，{d.skip}"
    assert st.qty != 0


def test_hard_stop_still_works_when_mfe_low():
    """MFE 未达阈值（<20）⇒ 硬止损 −40 照常，且 exit_path 是 stop_loss_taker。"""
    st = _state()
    limits = _limits(trail=20.0)
    d, _ = _tick(st, 99.50, limits)            # −50bp 直接硬止损
    assert any(f.is_flatten for f in d.fills)
    assert d.exit_path == "stop_loss_taker", d.exit_path


def test_mfe_reset_on_reentry_no_leak():
    """平仓后再开新仓：旧 MFE 不得泄漏（新仓 MFE<20 时只走硬止损）。"""
    st = _state()
    limits = _limits(trail=20.0)
    _tick(st, 100.30, limits)                  # MFE=30
    d, _ = _tick(st, 100.03, limits, now=1_000_045.0)   # 锁利平仓
    assert d.exit_path == "trail_lock_taker"
    # 重新开仓：均价 101
    st.qty = 0.5
    st.avg_px = st.avg_mid = 101.0
    st.opened_ts = 2_000_000.0
    d2, _ = _tick(st, 100.85, limits, now=2_000_015.0)  # −14.9bp：旧 MFE 若泄漏会误触尾随线
    assert not any(f.is_flatten for f in d2.fills), f"旧 MFE 泄漏导致误离场，{d2.skip}"
    # MFE 锚定新仓 avg_mid（101.0）：此 tick 浮盈 −14.9bp，MFE 应 ≤0 而不是残留的 +30。
    assert st.mfe_avg_mid == 101.0 and st.mfe_bp <= 0.0, \
        f"新仓 MFE 应重置到新仓口径（锚 101.0、MFE≤0），实际 {st.mfe_bp}/{st.mfe_avg_mid}"


def test_serialization_roundtrip_preserves_mfe():
    st = _state()
    st.mfe_bp = 35.0
    st.mfe_avg_mid = 99.5
    st2 = mmrunner.SymbolState.from_dict(st.to_dict())
    assert abs(st2.mfe_bp - 35.0) < 1e-6
    assert abs(st2.mfe_avg_mid - 99.5) < 1e-9
