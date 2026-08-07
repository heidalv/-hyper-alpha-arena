"""Prompt Registry — Swing/Trend Agent task 渲染单测。"""
import pytest


pytestmark = pytest.mark.unit


class TestPromptRegistryAgentTasks:
    @pytest.fixture(autouse=True)
    def _clear_manifest_cache(self):
        try:
            from backend.services.prompt_registry import _load_manifest
            _load_manifest.cache_clear()
        except Exception:
            pass
        yield
        try:
            from backend.services.prompt_registry import _load_manifest
            _load_manifest.cache_clear()
        except Exception:
            pass

    def test_render_swing_agent_task(self, monkeypatch):
        from unittest.mock import patch
        from backend.services.prompt_registry import get_prompt_registry

        monkeypatch.setenv("PROMPT_L2_ENABLED", "false")
        with patch("backend.services.prompt_l2_resolver.resolve_l2_prompt", return_value=None), \
             patch("backend.services.prompt_registry._try_load_l2_active_prompt", return_value=None):
            text = get_prompt_registry().render_task(
                "task_swing_agent",
                {
                    "symbol": "BTC",
                    "deep_context": "RSI_1h=55",
                    "compact_report": "mid_bias=bullish",
                    "orchestrator": {"mid_confidence": 0.7},
                    "evidence_block": "## 证据清单\n- rsi_1h: 55",
                },
            )
        assert "SwingAgent" in text
        assert "BTC" in text
        assert "RSI_1h=55" in text
        assert "mid_bias=bullish" in text
        assert "cited_fact_ids" in text

    def test_render_trend_direction_task(self):
        from backend.services.prompt_registry import get_prompt_registry

        text = get_prompt_registry().render_task(
            "task_trend_agent_direction",
            {
                "symbol": "ETH",
                "side_hint": "long",
                "macro_block": "宏观周期：扩张",
                "deep_context": "trend_1d=up",
                "compact_report": "orchestrator summary",
                "orchestrator": {"long_view": {"bias": "bullish"}},
                "evidence_block": "## 证据\n- trend_1d",
            },
        )
        assert "TrendAgent" in text
        assert "ETH" in text
        assert "side=long" in text or "side_hint" in text or "long" in text
        assert "trend_score" in text
        assert "scenario_a" in text

    def test_render_agent_task_service_fallback(self, monkeypatch):
        from backend.services import agent_prompt_service as aps

        def _boom(*_a, **_k):
            raise RuntimeError("registry down")

        monkeypatch.setattr(
            "backend.services.prompt_registry.get_prompt_registry",
            lambda: type("X", (), {"render_task": _boom})(),
        )
        out = aps.render_agent_task(
            "task_swing_agent",
            {"symbol": "SOL"},
            consumer="test",
            fallback_text="FALLBACK_PROMPT",
        )
        assert out == "FALLBACK_PROMPT"
