"""AI 覆盖层：LLM 输出确定性验证（#11）、LLM 供应商工厂 + 语义缓存（#13）、
DSPy 认知层编译器（#15）。"""
from backend.services.ai.market_data_verifier import (
    MarketDataVerifier,
    VerificationResult,
)
from backend.services.ai.semantic_cache import HashingEmbedder, SemanticCache
from backend.services.ai.llm_factory import (
    CachedLLMClient,
    CallableLLMClient,
    FailoverLLMClient,
    LLMClient,
    get_llm,
    get_client,
    register_client,
    CAPABILITY_MAP,
)
from backend.services.ai.prompt_compiler import (
    CompiledPrompt,
    Signature,
    TradingPromptCompiler,
)

__all__ = [
    "MarketDataVerifier",
    "VerificationResult",
    "HashingEmbedder",
    "SemanticCache",
    "LLMClient",
    "CallableLLMClient",
    "FailoverLLMClient",
    "CachedLLMClient",
    "get_llm",
    "get_client",
    "register_client",
    "CAPABILITY_MAP",
    "CompiledPrompt",
    "Signature",
    "TradingPromptCompiler",
]
