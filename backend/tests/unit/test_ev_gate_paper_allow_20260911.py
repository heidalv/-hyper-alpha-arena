# -*- coding: utf-8 -*-
"""[2026-09-11] mid 车道 EV 闸 paper 影子放行契约测试。

现场：MIDLONG_EV_ENFORCE_MID=true + mid EV≈-2.60%（校准器旧亏损样本）→ 硬拦
→ mid 零成交 → 校准器永远拿不到新样本 → EV 恒负（自我锁死）。
用户定调「模拟盘=收集交易数据」⇒ paper 下 mid 影子放行；live 硬拦不变。

契约：
- enforce_mid=true + paper_mode=true + PAPER_ALLOW=true（默认）→ 放行（影子记录）。
- enforce_mid=true + paper_mode=false（live）→ 仍硬拦（保护不变）。
- PAPER_ALLOW=false → paper 也硬拦（回滚档）。
- enforce_mid=false → 照旧影子放行（既有行为）。
"""
import pytest

import backend.config.settings as settings
from backend.services.decision_core.midlong_ev_gate import MidLongEvGate, midlong_ev_gate


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_EV_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION", False, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_MID_PAPER_ALLOW", True, raising=False)
    monkeypatch.setattr(settings, "SWING_EV_MIN_PCT", 0.0005, raising=False)
    monkeypatch.setattr(settings, "SWING_EV_TP_REALIZATION", 0.70, raising=False)
    monkeypatch.setattr(settings, "SWING_EV_SL_REALIZATION", 1.0, raising=False)
    midlong_ev_gate._stats.clear()


def _eval(monkeypatch, *, enforce_mid, paper_mode, p_win=None):
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_MID", enforce_mid, raising=False)
    kw = dict(
        nature="swing", calib_nature="swing", symbol="UNI", score=50.0,
        direction="long", tp_pct=0.20, sl_pct=0.10, paper_mode=paper_mode,
    )
    if p_win is not None:
        kw["p_win_override"] = p_win
    return midlong_ev_gate.evaluate(**kw)


def test_paper_mid_shadow_allows_even_negative_ev(monkeypatch):
    """enforce_mid=true、EV 为负、paper → 放行（影子）。"""
    dec = _eval(monkeypatch, enforce_mid=True, paper_mode=True, p_win=0.40)
    assert dec.allowed is True
    assert dec.breakdown.get("shadow_lane_switch") is True


def test_live_mid_still_hard_blocks_negative_ev(monkeypatch):
    """enforce_mid=true、EV 为负、live → 拦截（保护不变）。"""
    dec = _eval(monkeypatch, enforce_mid=True, paper_mode=False, p_win=0.40)
    assert dec.allowed is False
    assert "EV=" in dec.reason


def test_rollback_flag_paper_also_blocks(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_MID_PAPER_ALLOW", False, raising=False)
    dec = _eval(monkeypatch, enforce_mid=True, paper_mode=True, p_win=0.40)
    assert dec.allowed is False


def test_enforce_off_unchanged(monkeypatch):
    """enforce_mid=false → 影子放行（既有行为，paper/live 一致）。"""
    for paper_mode in (True, False):
        dec = _eval(monkeypatch, enforce_mid=False, paper_mode=paper_mode, p_win=0.40)
        assert dec.allowed is True
        assert dec.breakdown.get("shadow_lane_switch") is True


def test_positive_ev_passes_both_modes(monkeypatch):
    for paper_mode in (True, False):
        dec = _eval(monkeypatch, enforce_mid=True, paper_mode=paper_mode, p_win=0.80)
        assert dec.allowed is True
