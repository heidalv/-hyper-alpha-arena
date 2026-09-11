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


# [2026-09-02] 2026-08-23 修复后 Flash 在 auto 模式下**默认关闭思考**（Flash 单次
# 111s+ reasoning 触发平台"响应慢"→杀后端重启循环，短线停摆的根源）。
# deep/max 档的思考策略改用非 Flash 的 V4 模型验证；Flash 另加回归用例。
_V4_PRO = "deepseek-v4-pro"


def test_auto_flash_deep_tier_stays_fast(monkeypatch):
    """Flash + deep 档 + auto → 不思考（08-23 事故回归）。"""
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    p = resolve_thinking_policy("deepseek-v4-flash", "TrendAgent:direction")
    assert p["apply"] is True
    assert p["thinking_enabled"] is False
    assert p["tier"] == "flash_fast"
    # 显式 enabled 可强制恢复
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "enabled")
    p2 = resolve_thinking_policy("deepseek-v4-flash", "TrendAgent:direction")
    assert p2["thinking_enabled"] is True


def test_auto_deep_uses_high(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    p = resolve_thinking_policy(_V4_PRO, "TrendAgent:direction")
    assert p["thinking_enabled"] is True
    assert p["reasoning_effort"] == "high"


def test_auto_max_uses_max(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    p = resolve_thinking_policy(_V4_PRO, "hermes:evolve")
    assert p["thinking_enabled"] is True
    assert p["reasoning_effort"] == "max"
    assert p["bump_max_tokens"] is True


def test_apply_payload_max_bumps_tokens(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    monkeypatch.setenv("DEEPSEEK_REASONING_EFFORT", "auto")
    monkeypatch.setenv("DEEPSEEK_THINKING_MAX_TOKENS_FLOOR", "16000")
    payload = {
        "model": _V4_PRO,
        "messages": [],
        "max_completion_tokens": 2000,
    }
    apply_deepseek_thinking_to_payload(payload, model=_V4_PRO, caller="opencode")
    assert payload["thinking"]["type"] == "enabled"
    assert payload["reasoning_effort"] == "max"
    assert payload["max_completion_tokens"] >= 16000


def test_non_deepseek_noop(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "auto")
    p = resolve_thinking_policy("gpt-4o", "TrendAgent")
    assert p["apply"] is False
