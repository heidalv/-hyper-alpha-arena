"""Agent 证据链 + Fact Guard 单测。"""
import pytest


pytestmark = pytest.mark.unit


def _facts_with_rsi(rsi: float):
    from backend.services.agent_evidence_builder import AgentEvidenceFact
    return [
        AgentEvidenceFact("rsi_1h", "indicators_1h", rsi, True),
        AgentEvidenceFact("mid_bias", "orchestrator", "bullish", True),
        AgentEvidenceFact("ema_trend_1h", "orchestrator", "bullish", True),
    ]


class TestAgentFactGuard:
    def test_shadow_mode_never_blocks(self, monkeypatch):
        monkeypatch.setenv("AGENT_FACT_GUARD_MODE", "shadow")
        from backend.services.agent_fact_guard import verify_agent_decision

        fg = verify_agent_decision(
            action="buy",
            confidence=70,
            reasoning="RSI超卖反弹",
            cited_fact_ids=["rsi_1h"],
            facts=_facts_with_rsi(40.0),
            agent_type="swing",
        )
        assert fg.allow is True
        assert fg.mode == "shadow"
        assert "FG_RSI_OVERSOLD" in fg.violations

    def test_enforce_blocks_false_oversold_claim(self, monkeypatch):
        monkeypatch.setenv("AGENT_FACT_GUARD_MODE", "enforce")
        from importlib import reload
        import backend.config.settings as settings_mod
        reload(settings_mod)

        from backend.services.agent_fact_guard import verify_agent_decision

        fg = verify_agent_decision(
            action="buy",
            confidence=70,
            reasoning="明显超卖",
            cited_fact_ids=["rsi_1h"],
            facts=_facts_with_rsi(42.0),
            agent_type="swing",
            min_confidence=55,
        )
        assert fg.adjusted_confidence == 55
        assert fg.allow is True

    def test_missing_cited_fact_violation(self, monkeypatch):
        monkeypatch.setenv("AGENT_FACT_GUARD_MODE", "shadow")
        from backend.services.agent_fact_guard import verify_agent_decision

        fg = verify_agent_decision(
            action="buy",
            confidence=60,
            reasoning="test",
            cited_fact_ids=["nonexistent_fact"],
            facts=_facts_with_rsi(30),
            agent_type="swing",
        )
        assert any(v.startswith("FG_MISSING_DATA") for v in fg.violations)


class TestAgentEvidenceBuilder:
    def test_build_swing_evidence_core_ids(self):
        from backend.services.agent_evidence_builder import build_swing_evidence

        envs = {
            "BTC": {
                "orchestrator": {"mid_bias": "bullish", "mid_confidence": 0.6},
                "funding_rate": 0.0001,
                "regime": "trending",
                "indicators_1h": {"rsi": 55, "ema_trend": "bullish", "vol_ratio": 1.2},
                "indicators_4h": {"rsi": 58},
            }
        }
        facts = build_swing_evidence("BTC", envs)
        ids = {f.id for f in facts}
        for required in (
            "rsi_1h", "rsi_4h", "ema_trend_1h", "mid_bias",
            "mid_confidence", "funding_rate", "regime",
        ):
            assert required in ids

    def test_format_evidence_for_prompt(self):
        from backend.services.agent_evidence_builder import (
            AgentEvidenceFact,
            format_evidence_for_prompt,
        )

        block = format_evidence_for_prompt([
            AgentEvidenceFact("rsi_1h", "indicators_1h", 32, True),
        ])
        assert "rsi_1h" in block
        assert "cited_fact_ids" in block
