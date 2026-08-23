# -*- coding: utf-8 -*-
"""2026-08-23 本地LLM最大化改造测试：解析分层 / qwen3 适配 / usage 排序语义。"""
from __future__ import annotations

import pytest

from backend.services.llm_config_service import (
    LLMConfig,
    _effective_max_tokens,
    _normalize_ollama_response,
    _is_ollama_qwen3,
)


def _cfg(pid, provider, model, api_key="k"):
    return LLMConfig(id=pid, name=f"c{pid}", provider=provider, model=model,
                     base_url="http://x/v1", api_key=api_key)


def test_is_ollama_qwen3():
    assert _is_ollama_qwen3(_cfg(1, "ollama", "qwen3:14b")) is True
    assert _is_ollama_qwen3(_cfg(2, "ollama", "qwen2.5:7b")) is False
    assert _is_ollama_qwen3(_cfg(3, "deepseek", "qwen3-chat")) is False


def test_effective_max_tokens_qwen3_floor():
    assert _effective_max_tokens(_cfg(1, "ollama", "qwen3:14b"), 200) == 1024
    assert _effective_max_tokens(_cfg(1, "ollama", "qwen3:14b"), 2000) == 2000
    assert _effective_max_tokens(_cfg(2, "deepseek", "deepseek-v4-flash"), 200) == 200


def test_normalize_ollama_response_reasoning_promotion():
    resp = {"choices": [{"message": {"role": "assistant", "content": "",
                                      "reasoning": "分析结论：中性"}}]}
    out = _normalize_ollama_response(resp)
    assert out["choices"][0]["message"]["content"] == "分析结论：中性"


def test_normalize_ollama_response_keeps_content():
    resp = {"choices": [{"message": {"role": "assistant", "content": "有内容"}}]}
    out = _normalize_ollama_response(resp)
    assert out["choices"][0]["message"]["content"] == "有内容"


def test_normalize_none_and_empty():
    assert _normalize_ollama_response(None) is None
    assert _normalize_ollama_response({}) == {}


class _FakeUsageResolver:
    """monkeypatch get_llm_config_for_usage 的假实现。"""

    def __init__(self, by_key):
        # by_key: {(usage, provider)} -> LLMConfig | None
        self.by_key = by_key

    def __call__(self, usage, account_id=None, tier="quick", tenant_id=None, provider=None):
        return self.by_key.get((usage, provider))


def test_local_first_basic(monkeypatch):
    from backend.services import llm_config_service as svc
    local = _cfg(84, "ollama", "qwen3:14b")
    cloud = _cfg(17, "deepseek", "deepseek-v4-flash")
    monkeypatch.setattr(svc, "get_llm_config_for_usage", _FakeUsageResolver({
        ("kline_analysis", "ollama"): local,
        ("kline_analysis", None): cloud,
    }))
    l, c = svc.get_llm_config_local_first("kline_analysis", tenant_id=326)
    assert l is not None and l.id == 84
    assert c is not None and c.id == 17


def test_local_first_no_local_returns_cloud(monkeypatch):
    from backend.services import llm_config_service as svc
    cloud = _cfg(17, "deepseek", "deepseek-v4-flash")
    monkeypatch.setattr(svc, "get_llm_config_for_usage", _FakeUsageResolver({
        ("kline_analysis", "ollama"): None,
        ("kline_analysis", None): cloud,
    }))
    l, c = svc.get_llm_config_local_first("kline_analysis", tenant_id=326)
    assert l is None
    assert c is not None and c.id == 17


def test_local_first_same_config_no_fallback(monkeypatch):
    from backend.services import llm_config_service as svc
    only = _cfg(84, "ollama", "qwen3:14b")
    monkeypatch.setattr(svc, "get_llm_config_for_usage", _FakeUsageResolver({
        ("journal", "ollama"): only,
        ("journal", None): only,
    }))
    l, c = svc.get_llm_config_local_first("journal", tenant_id=326)
    assert l is not None and l.id == 84
    assert c is None


def test_usage_resolution_non_default_precedence():
    """[2026-08-23 排序修复] usage 解析：非默认专属绑定优先于默认宽 scope。
    factor_mining → 本地 84（此前恒落默认 17）。需要真实 DB（本仓库单测环境）。
    """
    from backend.services.llm_config_service import get_llm_config_for_usage
    try:
        cfg = get_llm_config_for_usage("factor_mining", tenant_id=326)
    except Exception as e:
        pytest.skip(f"DB 不可用: {e}")
    assert cfg is not None
    assert getattr(cfg, "provider", None) == "ollama", "factor_mining 应命中本地 ollama"
