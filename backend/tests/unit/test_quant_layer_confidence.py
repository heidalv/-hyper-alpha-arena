"""[阶段3b] quant_layer llm_qual confidence 提升单元测试。

阶段3a flagged: llm_qual signal confidence=0.5 → 有效权重=0.30×0.5=0.15，
LLM 全权重未实现。阶段3b 提升至 0.85，使 LLM 全权重（0.30）真正生效。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import quant_layer as Q
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


def _thesis(direction: str = "long") -> ThesisDTO:
    # [2026-09-02] P5 修复后 llm_v 以 thesis.direction 为真语义：neutral → 0.5；
    # long/short → 0.5 + (conviction-50)/100 再夹到方向侧（≥0.55 / ≤0.45）。
    # 原 helper 不设 direction（默认 neutral）→ value 恒 0.5，与 0.80 的断言冲突。
    return ThesisDTO(
        thesis_id="t1",
        session_id="sess1",
        symbol="BTC",
        tier="long",
        direction=direction,
        llm_conviction=80,  # long → llm_v = 0.5 + (80-50)/100 = 0.80
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
        # long: llm_v = 0.5 + (80-50)/100 = 0.80
        assert llm_qual.value == pytest.approx(0.80)

    def test_llm_qual_value_neutral_is_half_and_short_clamped(self, monkeypatch):
        """P5 方向语义（**旧路径**）：neutral → 0.5；short 夹到 ≤0.45。

        [2026-09-18 根因修复后本用例必须显式回滚] 新语义规定：
        **方向不可执行（日线非 down 时的 mid/long 空头）不参与投票**，故 short 在
        BTC 这类非 down regime 上会得到 0.5 而非 ≤0.45。这里用回滚开关
        `MLTO_LLM_UNEXEC_VOTE=1` 保留对旧夹取逻辑的覆盖（新语义由下面两个用例覆盖）。
        """
        monkeypatch.setenv("MLTO_LLM_UNEXEC_VOTE", "1")
        neutral = next(s for s in compute(_packet(), _thesis("neutral"), db=None) if s.name == "llm_qual")
        assert neutral.value == pytest.approx(0.5)
        short = next(s for s in compute(_packet(), _thesis("short"), db=None) if s.name == "llm_qual")
        assert short.value <= 0.45

    def test_unexecutable_short_does_not_vote(self, monkeypatch):
        """[2026-09-18 根因修复] 必然被 regime 闸拒的空头**不得参与投票**。

        实测背景：近 3h 133 条主脑决策里 short 100 条（单一标的 VIRTUAL 占 80），
        它们必然被 `midlong_short_regime_block` 拒；而旧逻辑把 short 夹到
        llm_qual≤0.45 ⇒ ai_governed（权重 0.60）下持续压低 composite
        ⇒ **连该币合规的多头提案也被拖成 WAIT**。
        """
        monkeypatch.delenv("MLTO_LLM_UNEXEC_VOTE", raising=False)
        # 强制"该币日线非 down" ⇒ 空头不可执行
        monkeypatch.setattr(Q, "_short_direction_unexecutable", lambda s, t: True)
        short = next(s for s in compute(_packet(), _thesis("short"), db=None) if s.name == "llm_qual")
        assert short.value == pytest.approx(0.5), "不可执行的方向应等价中性（不投票）"
        # 对照：可执行时仍按方向侧夹取
        monkeypatch.setattr(Q, "_short_direction_unexecutable", lambda s, t: False)
        short2 = next(s for s in compute(_packet(), _thesis("short"), db=None) if s.name == "llm_qual")
        assert short2.value <= 0.45

    def test_unexecutable_helper_semantics(self, monkeypatch):
        """助手口径必须与 `midlong_circuit_gate` 同源，且 fail-safe 回 False。"""
        monkeypatch.delenv("MLTO_LLM_UNEXEC_VOTE", raising=False)
        import backend.services.full_auto.midlong_circuit_gate as CG

        # short tier 不受本闸约束
        assert Q._short_direction_unexecutable("BTC", "short") is False
        # regime_gated + 非 down ⇒ 不可执行
        monkeypatch.setattr(CG, "_short_mode", lambda: "regime_gated")
        for reg, want in (("up", True), ("chop", True), ("unknown", True), ("", True),
                          ("down", False)):
            monkeypatch.setattr(CG, "_daily_regime", lambda s, _r=reg: _r)
            assert Q._short_direction_unexecutable("BTC", "long") is want, reg
        # 非 regime_gated 模式 ⇒ 不干预（旧行为）
        monkeypatch.setattr(CG, "_short_mode", lambda: "conditional")
        monkeypatch.setattr(CG, "_daily_regime", lambda s: "up")
        assert Q._short_direction_unexecutable("BTC", "long") is False
        # 回滚开关 ⇒ 恒 False
        monkeypatch.setattr(CG, "_short_mode", lambda: "regime_gated")
        monkeypatch.setenv("MLTO_LLM_UNEXEC_VOTE", "1")
        assert Q._short_direction_unexecutable("BTC", "long") is False

    def test_prompt_hint_forbids_unexecutable_direction(self):
        """[2026-09-18 根因修复·治生成] 主脑提示词不得再**许可**写 bearish。

        旧文案「direction 可写 bearish，但 recommend_open=true 的空头论题不会成交」
        是失效根源：下游把 direction 当可执行意图，许可等于制造必然被拒的提案
        （实测 LLM 仍提 recommend_open=true 的空头，快照 id=461133/461134）。
        """
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "backend" / "services" / "mlto"
               / "brain.py").read_text(encoding="utf-8")
        i = src.index("【风控硬约束】")
        seg = src[i:i + 700]
        assert "必须写 neutral" in seg, "未强制 neutral ⇒ 仍会产生必然被拒的空头"
        assert "可写 bearish" not in seg, "旧的许可文案仍在"
        assert "reasoning" in seg, "应指明 bearish 观点写进 reasoning"

    def test_llm_qual_source_still_llm(self):
        signals = compute(_packet(), _thesis(), db=None)
        llm_qual = next(s for s in signals if s.name == "llm_qual")
        assert llm_qual.source == "llm"
