# -*- coding: utf-8 -*-
"""
T7 验证：LLM 止损参数直通（v6 计划 6.3 第 3 项）。

开仓用 LLM exit_plan.sl_pct（ATR 下限硬校验），structure_stops 降级为兜底：
  1. qual_layer 解析 LLM exit_plan.sl_pct/tp_pct（v3 内嵌 / v2 扁平双格式）
  2. thesis_store 透传落库（>0 更新，0.0 不覆盖历史有效值）
  3. orchestrator._llm_stops：LLM sl → ATR floor → TP≥2×SL → structure 兜底

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_llm_stops_direct.py -q
"""
from __future__ import annotations

import pytest

from backend.services.mlto import qual_layer, thesis_store
from backend.services.mlto.orchestrator import _llm_stops, _structure_stops
from backend.services.mlto.types import PerceptionPacket, QualUpdateResult, ThesisDTO


def _packet(symbol="BTC", tier="mid", atr_1d_pct=0.03, price=100.0):
    return PerceptionPacket(
        symbol=symbol,
        tier=tier,
        session_id="sess_t7",
        ts=0.0,
        price=price,
        market_summary_sym={"atr_1d_pct": atr_1d_pct},
        orchestrator={},
        quant_brief={},
        analyst_reports={},
        trading_mode="paper",
    )


def _thesis(sl=0.0, tp=0.0):
    return ThesisDTO(
        thesis_id="t7", session_id="sess_t7", symbol="BTC", tier="mid",
        sl_pct=sl, tp_pct=tp,
    )


# ═══════════════════════════════════════════════════════════════════
# 1. qual_layer exit_plan 解析（v3/v2 双格式）
# ═══════════════════════════════════════════════════════════════════

def test_parse_sl_v3_exit_plan_inner():
    """v3：sl 嵌在 exit_plan 内。"""
    assert qual_layer._parse_exit_plan_sl({"exit_plan": {"sl_pct": 0.045}}) == 0.045


def test_parse_sl_v3_tp_sl_proposal_inner():
    """v3 别名：tp_sl_proposal 内嵌。"""
    assert qual_layer._parse_exit_plan_sl({"tp_sl_proposal": {"sl_pct": 0.06}}) == 0.06


def test_parse_sl_v2_flat():
    """v2：扁平 sl_pct。"""
    assert qual_layer._parse_exit_plan_sl({"sl_pct": 0.035}) == 0.035


def test_parse_sl_missing_returns_zero():
    """LLM 未提供 → 0.0（触发 structure_stops 兜底）。"""
    assert qual_layer._parse_exit_plan_sl({}) == 0.0
    assert qual_layer._parse_exit_plan_sl(None) == 0.0


def test_parse_tp_prefers_stages_first():
    """tp_stages[0].pct 优先于扁平 tp_pct。"""
    raw = {"exit_plan": {"tp_stages": [{"pct": 0.12}, {"pct": 0.24}]}, "tp_pct": 0.07}
    assert qual_layer._parse_exit_plan_tp(raw) == 0.12


def test_parse_tp_plan_flat_then_root():
    """无 stages 时：plan.tp_pct → 根级 tp_pct。"""
    assert qual_layer._parse_exit_plan_tp({"exit_plan": {"tp_pct": 0.15}}) == 0.15
    assert qual_layer._parse_exit_plan_tp({"tp_pct": 0.08}) == 0.08
    assert qual_layer._parse_exit_plan_tp({}) == 0.0


def test_parse_result_carries_exit_plan():
    """_parse_result 完整链路：LLM raw → QualUpdateResult.sl_pct/tp_pct。"""
    raw = {
        "direction": "long",
        "thesis_summary": "s",
        "exit_plan": {"sl_pct": 0.04, "tp_stages": [{"pct": 0.10}]},
    }
    r = qual_layer._parse_result(raw)
    assert isinstance(r, QualUpdateResult)
    assert r.sl_pct == 0.04
    assert r.tp_pct == 0.10


# ═══════════════════════════════════════════════════════════════════
# 2. thesis_store 透传落库
# ═══════════════════════════════════════════════════════════════════

def test_apply_llm_update_persists_sl_tp():
    """LLM 给出非零 sl/tp → thesis 更新。"""
    t = _thesis()
    qual = QualUpdateResult(direction="long", thesis_summary="s", sl_pct=0.04, tp_pct=0.10)
    thesis_store.apply_llm_update(t, qual)
    assert t.sl_pct == 0.04
    assert t.tp_pct == 0.10


def test_apply_llm_update_zero_does_not_overwrite():
    """LLM 本轮漏字段（0.0）→ 不覆盖 thesis 历史有效值。"""
    t = _thesis(sl=0.05, tp=0.12)
    qual = QualUpdateResult(direction="neutral", thesis_summary="", sl_pct=0.0, tp_pct=0.0)
    thesis_store.apply_llm_update(t, qual)
    assert t.sl_pct == 0.05
    assert t.tp_pct == 0.12


def test_thesis_to_dict_includes_sl_tp():
    t = _thesis(sl=0.04, tp=0.09)
    d = t.to_dict()
    assert d["sl_pct"] == 0.04
    assert d["tp_pct"] == 0.09


# ═══════════════════════════════════════════════════════════════════
# 3. orchestrator._llm_stops：直通 + ATR floor + 兜底
# ═══════════════════════════════════════════════════════════════════

def test_llm_sl_pass_through_when_wide_enough():
    """LLM sl=0.10 ≥ ATR×1.5（0.045）→ 原样直通。"""
    t = _thesis(sl=0.10, tp=0.25)
    sl, tp = _llm_stops(t, _packet(atr_1d_pct=0.03), "buy")
    assert sl == pytest.approx(0.10)
    assert tp == pytest.approx(0.25)


def test_atr_floor_lifts_narrow_llm_sl():
    """LLM sl=0.02 < ATR×1.5（0.045）→ 硬抬到 0.045。"""
    t = _thesis(sl=0.02, tp=0.05)
    sl, tp = _llm_stops(t, _packet(atr_1d_pct=0.03), "buy")
    assert sl == pytest.approx(0.045), "窄 SL 应被 ATR floor 硬抬"
    assert tp == pytest.approx(0.09), "TP 至少 2×SL"


def test_tp_min_two_x_sl():
    """LLM tp=0.03 < 2×sl（0.20）→ 抬到 0.20。"""
    t = _thesis(sl=0.10, tp=0.03)
    sl, tp = _llm_stops(t, _packet(atr_1d_pct=0.01), "buy")
    assert tp == pytest.approx(0.20)


def test_llm_missing_falls_back_to_structure(monkeypatch):
    """LLM 未提供 sl → structure_stops 兜底（max(LLM,structure) 语义保留）。"""
    t = _thesis(sl=0.0, tp=0.0)

    class _FakeStop:
        @staticmethod
        def compute(*args, **kwargs):
            return (0.06, 0.15, 93.0, 107.0, "struct")

    monkeypatch.setattr(
        "backend.services.mid_long_structure_stop.mid_long_structure_stop", _FakeStop
    )
    sl, tp = _llm_stops(t, _packet(), "buy")
    assert sl == pytest.approx(0.06)
    assert tp == pytest.approx(0.15)


def test_non_trade_action_returns_zero():
    assert _llm_stops(_thesis(sl=0.05, tp=0.1), _packet(), "hold") == (0.0, 0.0)


def test_validation_failure_falls_back_to_structure(monkeypatch):
    """ATR 校验异常 → 降级结构止损，绝不让非法 SL 直通。"""
    t = _thesis(sl=0.05, tp=0.10)

    def _boom(*args, **kwargs):
        raise RuntimeError("atr missing")

    monkeypatch.setattr(
        "backend.services.mlto.midlong_trade_design.estimate_atr_1d_pct", _boom
    )

    class _FakeStop:
        @staticmethod
        def compute(*args, **kwargs):
            return (0.07, 0.18, 93.0, 107.0, "struct")

    monkeypatch.setattr(
        "backend.services.mid_long_structure_stop.mid_long_structure_stop", _FakeStop
    )
    sl, tp = _llm_stops(t, _packet(), "sell")
    assert sl == pytest.approx(0.07)
    assert tp == pytest.approx(0.18)


def test_structure_stops_still_works_directly(monkeypatch):
    """回归：_structure_stops 兜底函数本身未被破坏。"""
    class _FakeStop:
        @staticmethod
        def compute(*args, **kwargs):
            return (0.08, 0.20, 92.0, 108.0, "struct")

    monkeypatch.setattr(
        "backend.services.mid_long_structure_stop.mid_long_structure_stop", _FakeStop
    )
    sl, tp = _structure_stops(_packet(tier="mid"), "buy")
    assert sl == pytest.approx(0.08)
    assert tp == pytest.approx(0.20)
