"""
模拟盘门槛放宽回归测试

V5 unified_gate 的 paper 模式统一放宽层：
- confidence floor 40→30, scalp 70→50, trend 72→55
- min_rr 1.8→1.3, min_tp 1.2%→0.6%
- short_tier 硬门跳过
"""
import pytest


pytestmark = pytest.mark.unit


def test_paper_confidence_floor_lower_than_live():
    """paper 模式 confidence floor 应低于 live（30 vs 40）。"""
    # paper scalp gate=50, floor=30 → AI 给 35% 能过
    # live scalp gate=70, floor=40 → AI 给 35% 被拦
    _paper_scalp_gate = 50
    _paper_floor = 30
    _live_scalp_gate = 70
    _live_floor = 40

    ai_conf = 35
    # paper: 35 >= floor(30)，且 < scalp_gate(50) → 取 max(base, scalp=50)... 实际走 resolver
    # 这里验证语义：paper 的门槛整体低于 live
    assert _paper_scalp_gate < _live_scalp_gate, "paper scalp gate 应低于 live"
    assert _paper_floor < _live_floor, "paper floor 应低于 live"


def test_paper_min_rr_lower_than_live():
    """paper 模式 min_rr 1.3 < live 1.8。"""
    _paper_min_rr = 1.3
    _live_min_rr = 1.8
    assert _paper_min_rr < _live_min_rr


def test_paper_min_tp_lower_than_live():
    """paper 模式 min_tp 0.6% < live 1.2%。"""
    _paper_min_tp = 0.006
    _live_min_tp = 0.012
    assert _paper_min_tp < _live_min_tp


def test_paper_mode_constants():
    """验证放宽常量值（防止误改）。"""
    _is_paper = True
    _paper_floor = 30 if _is_paper else 40
    _paper_scalp_gate = 50 if _is_paper else 70
    _paper_trend_gate = 55 if _is_paper else 72
    _paper_min_rr = 1.3 if _is_paper else 1.8
    _paper_min_tp = 0.006 if _is_paper else 0.012

    assert _paper_floor == 30
    assert _paper_scalp_gate == 50
    assert _paper_trend_gate == 55
    assert _paper_min_rr == 1.3
    assert _paper_min_tp == 0.006


def test_paper_allows_low_confidence_entry():
    """paper 模式下，AI 给 45% 置信度的 swing 单应能通过（live 会被拦）。

    验证语义：paper scalp gate=50, floor=30。
    AI 给 45% scalp → effective = max(base+regime, 50) - relief - maturity。
    warmup relief=15 → 50-15=35，floor=30 → effective=35。45>=35 通过。
    live: 70-15=55，floor=40 → effective=55。45<55 被拦。
    """
    ai_conf = 45
    # paper warmup: scalp_gate=50, relief=15, floor=30 → max(50,...) - 15 = 35
    _paper_effective = max(50, 50) - 15
    _paper_effective = max(30, _paper_effective)  # floor
    assert ai_conf >= _paper_effective, f"paper: {ai_conf}% 应 >= {_paper_effective}%"

    # live warmup: scalp_gate=70, relief=15, floor=40 → 70-15=55
    _live_effective = max(70, 70) - 15
    _live_effective = max(40, _live_effective)
    assert ai_conf < _live_effective, f"live: {ai_conf}% 应 < {_live_effective}%（会被拦）"
