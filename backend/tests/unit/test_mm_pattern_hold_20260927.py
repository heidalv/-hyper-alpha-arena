# -*- coding: utf-8 -*-
"""[h403 2026-09-27] #8/#15 分形态持有期（pattern_tag / p1_hold_sec / p45_hold_sec）。

规则：开仓时刻按 mid_hist 形态标记（P4/P5 双触+挤压 ⇒ "P45"；|300s 趋势|≥p1_th
⇒ "P1"；研究口径固定阈值、与闸门参数无关）。超时上界 = 标记对应参数（0=用
max_one_side_seconds，旧行为逐字）。平仓即清标记。
依据：h360b（P4/P5 fixed300 增量 +0.20~0.33bp/腿）、h360+h373（P1 第一半窗红利）。
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


def _tick(st, mid, now, limits):
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits)


# ── 纯检测函数 ────────────────────────────────────────────────────────────

def test_detect_p4_double_touch():
    # 120s 窗（9 期）两次贴极值且现价贴高 ⇒ P45
    hist = [100.0, 100.4, 100.1, 100.5, 100.2, 100.7, 100.3, 100.7, 100.7]
    assert mmrunner._detect_pattern(hist) == "P45"


def test_detect_p1_trend():
    hist = [100.0 + 1.0 * i for i in range(21)]   # +20bp 300s 趋势
    assert mmrunner._detect_pattern(hist) == "P1"


def test_detect_none():
    hist = [100.0 + 0.001 * i for i in range(21)]
    assert mmrunner._detect_pattern(hist) == ""


# ── 标记写入 + 分形态超时 ──────────────────────────────────────────────────

def test_open_fill_tags_pattern():
    """空仓→持仓的开仓腿写入 pattern_tag（P1 趋势场景）。"""
    now = 1_000_100.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = [100.0 + 1.0 * i for i in range(21)]   # +20bp ⇒ P1
    st.quote_bid = 100.0
    st.quote_mid = 100.0
    st.quote_ts = now - 15.0
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
        seg_taker_sell=100.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=_limits())
    adds = [f for f in dec.fills if not f.is_flatten]
    assert adds, f"应有开仓腿，skip={dec.skip}"
    assert st.pattern_tag == "P1", f"标记应为 P1，实际 {st.pattern_tag!r}"


def test_p1_hold_60_timeout_earlier_than_default():
    """P1 仓 + p1_hold_sec=60：75s 时超时生效（默认 90s 不生效）。"""
    now = 1_000_200.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 75.0
    st.pattern_tag = "P1"
    st.mid_hist = [100.0] * 20
    dec, _ = _tick(st, 100.0, now, _limits(p1_hold_sec=60.0))
    assert "timeout_maker_only" in (dec.skip or ""), dec.skip


def test_p1_hold_zero_uses_default():
    """p1_hold_sec=0：P1 标记也不改超时（旧行为）。75s < 90s ⇒ 不触发。"""
    now = 1_000_200.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 75.0
    st.pattern_tag = "P1"
    st.mid_hist = [100.0] * 20
    dec, _ = _tick(st, 100.0, now, _limits(p1_hold_sec=0.0))
    assert "timeout_maker_only" not in (dec.skip or ""), dec.skip


def test_p45_hold_300_extends():
    """P45 仓 + p45_hold_sec=300：120s 时默认（90s）会超时、扩展后不超时。"""
    now = 1_000_300.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 120.0
    st.pattern_tag = "P45"
    st.mid_hist = [100.0] * 20
    dec, _ = _tick(st, 100.0, now, _limits(p45_hold_sec=300.0))
    assert "timeout_maker_only" not in (dec.skip or ""), dec.skip
    # 参数为 0 ⇒ 默认 90s ⇒ 120s 时超时生效
    dec2, _ = _tick(st, 100.0, now, _limits(p45_hold_sec=0.0))
    assert "timeout_maker_only" in (dec2.skip or ""), dec2.skip


def test_tag_cleared_when_flat():
    """平仓后下一 tick MFE 空仓分支清除标记。"""
    now = 1_000_200.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.0
    st.pattern_tag = "P1"
    st.mid_hist = [100.0] * 20
    _tick(st, 100.0, now, _limits())
    assert st.pattern_tag == "", f"空仓应清标记，实际 {st.pattern_tag!r}"


def test_serialization_preserves_pattern_tag():
    st = mmrunner.SymbolState(symbol="BTC")
    st.pattern_tag = "P45"
    st2 = mmrunner.SymbolState.from_dict(st.to_dict())
    assert st2.pattern_tag == "P45"
