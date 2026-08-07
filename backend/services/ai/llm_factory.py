"""
LLM 供应商工厂（整改#13）—— 对标 TradingAgents llm_clients/factory。

统一 LLMClient 接口 + 多供应商容错(failover) + 按任务难度路由 + 语义缓存包装。
provider 客户端惰性绑定（缺 key/SDK 时构造即报错，由 failover 自动跳过），
保证零风险：不改动现有 LLM 调用路径，除非调用方显式改用本工厂。

env：
  LLM_SEMANTIC_CACHE_ENABLED / LLM_CACHE_TTL_SECONDS / LLM_CACHE_THRESHOLD
  LLM_TASK_<TASK>=<provider>   —— 覆盖 CAPABILITY_MAP 路由
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    @abstractmethod
    def complete(self, prompt: str, **kwargs) -> str: ...


class CallableLLMClient(LLMClient):
    """把任意 callable(prompt, **kwargs)->str 适配成 LLMClient。"""

    def __init__(self, fn: Callable[..., str], name: str = "callable"):
        self._fn = fn
        self.name = name

    def complete(self, prompt: str, **kwargs) -> str:
        return self._fn(prompt, **kwargs)


class _BackendHelperClient(LLMClient):
    """惰性绑定现网 LLM 基础设施（llm_reasoning_helper）。绑定失败即抛错→failover 跳过。"""

    def __init__(self, provider: str):
        self.provider = provider
        self._fn: Optional[Callable[..., str]] = None

    def _bind(self):
        if self._fn is not None:
            return
        # 尝试从现有 helper 找一个 (prompt)->str 的补全函数；找不到则抛错
        try:
            from backend.services import llm_reasoning_helper as h  # type: ignore

            for cand in ("complete", "chat_completion", "get_completion", "ask", "reason"):
                fn = getattr(h, cand, None)
                if callable(fn):
                    self._fn = fn
                    return
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM provider {self.provider} 绑定失败: {e}")
        raise RuntimeError(f"LLM provider {self.provider} 无可用补全函数")

    def complete(self, prompt: str, **kwargs) -> str:
        self._bind()
        return self._fn(prompt, **kwargs)  # type: ignore


class DeepSeekClient(_BackendHelperClient):
    def __init__(self):
        super().__init__("deepseek")


class OpenAIClient(_BackendHelperClient):
    def __init__(self):
        super().__init__("openai")


class AnthropicClient(_BackendHelperClient):
    def __init__(self):
        super().__init__("anthropic")


class LocalVLLMClient(_BackendHelperClient):
    def __init__(self):
        super().__init__("local_vllm")


class FailoverLLMClient(LLMClient):
    """容错客户端：primary 失败自动切 fallback，全失败才抛错。"""

    def __init__(self, primary: LLMClient, fallbacks: Optional[List[LLMClient]] = None):
        self.primary = primary
        self.fallbacks = fallbacks or []

    def complete(self, prompt: str, **kwargs) -> str:
        errors = []
        for client in [self.primary] + self.fallbacks:
            try:
                return client.complete(prompt, **kwargs)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(client).__name__}:{e}")
                continue
        raise RuntimeError(f"All LLM providers failed: {errors}")


class CachedLLMClient(LLMClient):
    """带语义缓存的 LLM 客户端。"""

    def __init__(self, inner: LLMClient, cache=None):
        from backend.services.ai.semantic_cache import SemanticCache

        self.inner = inner
        if cache is None:
            cache = SemanticCache(
                similarity_threshold=float(os.getenv("LLM_CACHE_THRESHOLD", "0.95")),
                ttl_seconds=float(os.getenv("LLM_CACHE_TTL_SECONDS", "60")),
            )
        self.cache = cache

    def complete(self, prompt: str, **kwargs) -> str:
        cached = self.cache.get(prompt)
        if cached is not None:
            return cached
        response = self.inner.complete(prompt, **kwargs)
        if response:
            self.cache.set(prompt, response)
        return response


# ---------------- 注册表 + 路由 ----------------
_CLIENT_FACTORIES: Dict[str, Callable[[], LLMClient]] = {
    "deepseek": DeepSeekClient,
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
    "local_vllm": LocalVLLMClient,
}

# 任务 → 推荐供应商档位（可被 env LLM_TASK_<TASK> 覆盖）
CAPABILITY_MAP = {
    "routine_tick": "local_vllm",     # 常规决策：小/快/便宜
    "deep_analysis": "deepseek",      # 深度分析：中等
    "ambiguous_event": "anthropic",   # 模糊事件：大模型
}

_CLIENT_CACHE: Dict[str, LLMClient] = {}


def register_client(name: str, factory: Callable[[], LLMClient]) -> None:
    _CLIENT_FACTORIES[name] = factory
    _CLIENT_CACHE.pop(name, None)


def get_client(name: str) -> LLMClient:
    if name not in _CLIENT_CACHE:
        factory = _CLIENT_FACTORIES.get(name)
        if factory is None:
            raise KeyError(f"未注册的 LLM 供应商: {name}")
        _CLIENT_CACHE[name] = factory()
    return _CLIENT_CACHE[name]


def _provider_for_task(task_type: str) -> str:
    env_key = f"LLM_TASK_{task_type.upper()}"
    return os.getenv(env_key) or CAPABILITY_MAP.get(task_type, "deepseek")


def get_llm(task_type: str, *, with_cache: Optional[bool] = None,
            fallbacks: Optional[List[str]] = None) -> LLMClient:
    """按任务类型路由到合适供应商，并按 env 包装 failover + 语义缓存。"""
    primary_name = _provider_for_task(task_type)
    primary = get_client(primary_name)

    fb_clients: List[LLMClient] = []
    for fb in (fallbacks or []):
        try:
            fb_clients.append(get_client(fb))
        except KeyError:
            continue
    client: LLMClient = FailoverLLMClient(primary, fb_clients) if fb_clients else primary

    if with_cache is None:
        with_cache = os.getenv("LLM_SEMANTIC_CACHE_ENABLED", "false").strip().lower() in (
            "1", "true", "yes", "on")
    if with_cache:
        client = CachedLLMClient(client)
    return client
