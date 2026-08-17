"""DeepSeek V4 ˼���ֲ���Ե��⡣"""
import os

from backend.services.deepseek_thinking import (
    apply_deepseek_thinking_to_payload,
    classify_thinking_tier,
    resolve_thinking_policy,
)


def test_classify_tiers():
    assert classify_thinking_tier("scalp_agent_summary") == "short"
    assert classify_thinking_tier("coin_select_platform") == "short"
    assert classify_thinking_tier("TrendAgent:direction") == "deep"
    assert classify_thinking_tier("MasterController:synthesize") == "deep"
    assert classify_thinking_tier("hermes_architecture") == "max"
    assert classify_thinking_tier("opencode_bridge") == "max"


def test_auto_short_disables_thinking(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    p = resolve_thinking_policy("deepseek-v4-flash", "scalp_flash_veto")
    assert p["apply"] is True
    assert p["thinking_enabled"] is False
    assert p["reasoning_effort"] is None


def test_auto_deep_uses_high(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    p = resolve_thinking_policy("deepseek-v4-flash", "TrendAgent:direction")
    assert p["thinking_enabled"] is True
    assert p["reasoning_effort"] == "high"


def test_auto_max_uses_max(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    p = resolve_thinking_policy("deepseek-v4-flash", "hermes:evolve")
    assert p["thinking_enabled"] is True
    assert p["reasoning_effort"] == "max"
    assert p["bump_max_tokens"] is True


def test_apply_payload_max_bumps_tokens(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    monkeypatch.setenv("DEEPSEEK_THINKING_MAX_TOKENS_FLOOR", "16000")
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [],
        "max_completion_tokens": 2000,
    }
    apply_deepseek_thinking_to_payload(payload, model="deepseek-v4-flash", caller="opencode")
    assert payload["thinking"]["type"] == "enabled"
    assert payload["reasoning_effort"] == "max"
    assert payload["max_completion_tokens"] >= 16000


def test_non_deepseek_noop(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    p = resolve_thinking_policy("gpt-4o", "TrendAgent")
    assert p["apply"] is False
