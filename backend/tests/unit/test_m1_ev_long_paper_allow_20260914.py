# -*- coding: utf-8 -*-
"""[M1 2026-09-14] long 车道 EV 闸 paper 影子放行 + 校准修复切点契约测试。

现场（2026-09-13）：trend 校准器 32 笔旧样本（修复前结构，胜率 0.375）→
p_win=0.393 → EV≈-1.3% < 门槛 +0.08% → long(trend_follow) 车道全部硬拦 →
tier=long 候选=8 成交=0（死亡螺旋）。mid 车道 9/11 已拿到 paper 影子放行，
long 车道没有 ⇒ 本批补齐同款政策 + 校准器 MIN_TS 修复切点。

契约：
- MIDLONG_EV_ENFORCE_LONG=false（默认）+ EV 为负 → paper 放行（影子），live 也放行（车道强制未开，与 mid enforce=false 同语义）。
- MIDLONG_EV_ENFORCE_LONG=true + paper + LONG_PAPER_ALLOW=true（默认）→ 放行（影子）。
- MIDLONG_EV_ENFORCE_LONG=true + live → 仍硬拦（保护不变）。
- LONG_PAPER_ALLOW=false → paper 也硬拦（回滚档）。
- 校准器 MIN_TS：早于切点的样本被排除；MIN_TS=0 旧行为不变。
"""
import pytest

import backend.config.settings as settings
from backend.services.decision_core.midlong_ev_gate import midlong_ev_gate


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_EV_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION", False, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_MID", False, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_MID_PAPER_ALLOW", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_LONG", False, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_LONG_PAPER_ALLOW", True, raising=False)
    monkeypatch.setattr(settings, "TREND_EV_MIN_PCT", 0.0008, raising=False)
    monkeypatch.setattr(settings, "TREND_EV_TP_REALIZATION", 0.60, raising=False)
    monkeypatch.setattr(settings, "TREND_EV_SL_REALIZATION", 1.0, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_EV_FALLBACK_RR", 2.0, raising=False)
    midlong_ev_gate._stats.clear()


def _eval(monkeypatch, *, enforce_long, paper_mode, p_win=None):
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_LONG", enforce_long, raising=False)
    kw = dict(
        nature="trend_follow", calib_nature="trend_follow", symbol="SOL", score=50.0,
        direction="long", tp_pct=0.14, sl_pct=0.07, paper_mode=paper_mode,
    )
    if p_win is not None:
        kw["p_win_override"] = p_win
    return midlong_ev_gate.evaluate(**kw)


def test_default_long_lane_shadow_allows_negative_ev(monkeypatch):
    """默认（enforce_long=false）：EV 负也影子放行（lane switch off，paper/live 一致）。"""
    for paper_mode in (True, False):
        dec = _eval(monkeypatch, enforce_long=False, paper_mode=paper_mode, p_win=0.40)
        assert dec.allowed is True
        assert dec.breakdown.get("shadow_lane_switch") is True


def test_paper_long_shadow_allows_even_negative_ev(monkeypatch):
    """enforce_long=true、EV 为负、paper → 放行（影子）。"""
    dec = _eval(monkeypatch, enforce_long=True, paper_mode=True, p_win=0.40)
    assert dec.allowed is True
    assert dec.breakdown.get("shadow_lane_switch") is True


def test_live_long_still_hard_blocks_negative_ev(monkeypatch):
    """enforce_long=true、EV 为负、live → 拦截（保护不变）。"""
    dec = _eval(monkeypatch, enforce_long=True, paper_mode=False, p_win=0.40)
    assert dec.allowed is False
    assert "EV=" in dec.reason


def test_rollback_flag_paper_also_blocks(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_EV_ENFORCE_LONG_PAPER_ALLOW", False, raising=False)
    dec = _eval(monkeypatch, enforce_long=True, paper_mode=True, p_win=0.40)
    assert dec.allowed is False


def test_positive_ev_passes_both_modes(monkeypatch):
    for paper_mode in (True, False):
        dec = _eval(monkeypatch, enforce_long=True, paper_mode=paper_mode, p_win=0.80)
        assert dec.allowed is True
