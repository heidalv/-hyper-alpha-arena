"""深度推理：流式 [DONE] 决定结束；快速模型才用固定超时预算。"""

import httpx

from backend.config.settings import compute_qaa_analyst_timeout
from backend.services.llm_config_service import (
    LLMConfig,
    build_httpx_timeout,
    resolve_llm_call_timeout,
    should_use_llm_streaming,
)


def test_should_use_streaming_for_reasoner():
    deep = LLMConfig(
        id=1, name="pro", provider="deepseek", model="deepseek-reasoner",
        base_url="https://api.deepseek.com", api_key="x",
    )
    quick = LLMConfig(
        id=2, name="flash", provider="deepseek", model="deepseek-chat",
        base_url="https://api.deepseek.com", api_key="x",
    )
    assert should_use_llm_streaming(deep) is True
    assert should_use_llm_streaming(quick) is False


def test_streaming_httpx_timeout_waits_for_done():
    """流式默认 read=None：不设固定秒数，等 SSE [DONE]。"""
    deep = LLMConfig(
        id=1, name="pro", provider="deepseek", model="deepseek-reasoner",
        base_url="https://api.deepseek.com", api_key="x",
    )
    t = build_httpx_timeout(deep, use_streaming=True)
    assert isinstance(t, httpx.Timeout)
    assert t.read is None


def test_non_streaming_uses_fixed_timeout():
    quick = LLMConfig(
        id=2, name="flash", provider="deepseek", model="deepseek-chat",
        base_url="https://api.deepseek.com", api_key="x",
    )
    assert resolve_llm_call_timeout(quick) >= 45
    budget = compute_qaa_analyst_timeout(symbol_count=5)
    assert budget > resolve_llm_call_timeout(quick)
