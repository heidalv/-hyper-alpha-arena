"""[阶段3b] quant_layer llm_qual confidence 提升单元测试。

阶段3a flagged: llm_qual signal confidence=0.5 → 有效权重=0.30×0.5=0.15，
LLM 全权重未实现。阶段3b 提升至 0.85，使 LLM 全权重（0.30）真正生效。
"""
from __future__ import annotations

import pytest

from backend.services.mlto.quant_layer import compute
from backend.services.mlto.types import PerceptionPacket, ThesisDTO


def _packet() -> PerceptionPacket:
    return PerceptionPacket(
        symbol="BTC",
        tier="long",
        session_id="sess1",
        ts=0.0,
        price=100000.0,
        market_summary_sym={"indicators_4h": {"rsi": 50}},
        orchestrator={},
        quant_brief={},
        analyst_reports={},
    )


def _thesis() -> ThesisDTO:
    return ThesisDTO(
        thesis_id="t1",
        session_id="sess1",
        symbol="BTC",
        tier="long",
        llm_conviction=80,  # → llm_v = 0.5 + (80-50)/100 = 0.80
    )


class TestLlmQualConfidence:
    def test_llm_qual_confidence_is_085(self):
        """[阶段3b] llm_qual signal confidence == 0.85（旧值 0.5）。"""
        signals = compute(_packet(), _thesis(), db=None)
        llm_qual = next(s for s in signals if s.name == "llm_qual")
        assert llm_qual.confidence == pytest.approx(0.85), (
            f"expected llm_qual confidence 0.85, got {llm_qual.confidence}"
        )

    def test_llm_qual_confidence_not_half_discounted(self):
        """confidence=0.85 → 有效权重 0.30×0.85=0.255（非 0.5 时的 0.15）。"""
        signals = compute(_packet(), _thesis(), db=None)
        llm_qual = next(s for s in signals if s.name == "llm_qual")
        # 0.85 明显高于 0.5（不再是半折）
        assert llm_qual.confidence > 0.75
        assert llm_qual.confidence != pytest.approx(0.5)

    def test_llm_qual_value_unchanged(self):
        """confidence 变更不影响 value 计算（仍由 llm_conviction 驱动）。"""
        signals = compute(_packet(), _thesis(), db=None)
        llm_qual = next(s for s in signals if s.name == "llm_qual")
        # llm_v = 0.5 + (80-50)/100 = 0.80
        assert llm_qual.value == pytest.approx(0.80)

    def test_llm_qual_source_still_llm(self):
        signals = compute(_packet(), _thesis(), db=None)
        llm_qual = next(s for s in signals if s.name == "llm_qual")
        assert llm_qual.source == "llm"
