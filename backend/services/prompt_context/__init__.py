# -*- coding: utf-8 -*-
"""[F321 2026-09-18 复查修复] 包级 re-export（此文件此前为 **0 字节**，git HEAD 亦然）。

后果（P0，已由复查独立复现）：`backend/services/ai_decision_service.py:2283` 的
`from backend.services.prompt_context import PromptContextBuilder, BuildInput`
必然抛 `ImportError: cannot import name 'PromptContextBuilder' from 'backend.services.prompt_context'`
⇒ **备用 LLM 决策车道永不渲染 prompt**（`call_ai_for_decision_with_fallback` 捕获后降级规则引擎；
直调点 `trading_commands.py:1036/2683` 直接抛）⇒ 该车道上的一切因子注入/引导改进都无法生效。

修法：把两个公共入口按原 import 路径显式导出（`PromptContextBuilder` 定义在 `builder.py:26`，
`BuildInput` 定义在 `types.py:18`），保持调用方零改动。
"""
from backend.services.prompt_context.builder import PromptContextBuilder  # noqa: F401
from backend.services.prompt_context.types import BuildInput, BuildResult  # noqa: F401

__all__ = ["PromptContextBuilder", "BuildInput", "BuildResult"]
