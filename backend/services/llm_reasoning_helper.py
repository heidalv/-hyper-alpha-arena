"""
LLM Reasoning Content Helper（公共复用模块）

从 ai_decision_service.py 提炼，供中长线 agent（SwingAgent/TrendAgent/DirectionAgent/
TradeRiskAgent）以及任何需要从 LLM 返回结构里捞回深度推理（reasoning_content / thinking /
cot 等）的模块统一复用。

为什么需要这个模块：
    reasoning 模型（DeepSeek R1/V4-Pro、Qwen QwQ、Claude thinking、Gemini thought、
    Grok-3-mini 等）把深度推理放在 message.reasoning_content / message.reasoning 等字段，
    而 message.content 只放最终结论。早期各 agent 的 _call_llm 只读 content，导致整条
    思维链被丢弃 —— 决策"看起来很浅"。本模块统一提取逻辑，保持单一真相源。

设计原则：
    - 纯函数，无 DB / 全局状态耦合，任何上下文（含同步 APScheduler 线程）均可调用。
    - 任何异常都返回空串，绝不阻塞主交易流（与 ai_decision_service 原行为一致）。
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_text_from_message(content: Any) -> str:
    """将 OpenAI / Anthropic 风格的 message content 归一化为纯字符串。

    支持 str / list（多模态数组）/ dict（嵌套结构）。非以上类型返回 ""。

    提炼自 ai_decision_service._extract_text_from_message，逻辑一字不改。
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                # Anthropic style: {"type": "text", "text": "..."}
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
                    continue

                # Some providers use {"type": "output_text", "content": "..."}
                content_value = item.get("content")
                if isinstance(content_value, str):
                    parts.append(content_value)
                    continue

                # Recursively handle nested content arrays
                nested = item.get("content")
                nested_text = extract_text_from_message(nested)
                if nested_text:
                    parts.append(nested_text)
        return "\n".join(parts)

    if isinstance(content, dict):
        # Direct text fields
        for key in ("text", "content", "value"):
            value = content.get(key)
            if isinstance(value, str):
                return value

        # Nested structures
        for key in ("text", "content", "parts"):
            nested = content.get(key)
            nested_text = extract_text_from_message(nested)
            if nested_text:
                return nested_text

    return ""


def extract_reasoning_content_safe(api_result: dict) -> str:
    """从 LLM API 返回结构里提取 reasoning（思维链），多厂商兼容。

    支持：OpenAI (o1/o3/gpt-5)、DeepSeek (R1/V4-Pro)、Qwen (QwQ)、
    Claude (thinking)、Gemini (thoughts)、Grok (3-mini)。

    Args:
        api_result: 完整的 API 返回 dict，形如
            {"choices": [{"message": {"content": ..., "reasoning_content": ...}}], ...}

    Returns:
        合并后的 reasoning 文本；无 reasoning 或任何异常时返回 ""（绝不抛错）。

    提炼自 ai_decision_service._extract_reasoning_content_safe（原为 call_ai_for_decision
    内部闭包，此处提升为模块级以便复用）。
    """
    try:
        reasoning_parts = []

        # Safe extraction: get choices and message with type checking
        choices = api_result.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            return ""

        choice_item = choices[0]
        if not isinstance(choice_item, dict):
            return ""

        msg = choice_item.get("message")
        if not isinstance(msg, dict):
            return ""

        # Strategy 1: OpenAI/DeepSeek/Qwen/Grok standard format
        # message.reasoning (OpenAI o1/o3/gpt-5)
        # message.reasoning_content (DeepSeek R1, Qwen QwQ, Grok 3-mini)
        try:
            reasoning_field = msg.get("reasoning")
            if reasoning_field:
                extracted = extract_text_from_message(reasoning_field)
                if extracted and extracted.strip():
                    reasoning_parts.append(extracted.strip())
        except Exception:
            pass

        try:
            reasoning_content_field = msg.get("reasoning_content")
            if reasoning_content_field:
                extracted = extract_text_from_message(reasoning_content_field)
                if extracted and extracted.strip():
                    reasoning_parts.append(extracted.strip())
        except Exception:
            pass

        # Strategy 2: Claude format - thinking blocks in content array
        # {"content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "..."}]}
        try:
            content_array = msg.get("content")
            if isinstance(content_array, list):
                for block in content_array:
                    if isinstance(block, dict) and block.get("type") == "thinking":
                        thinking_text = block.get("thinking")
                        if thinking_text and isinstance(thinking_text, str) and thinking_text.strip():
                            reasoning_parts.append(thinking_text.strip())
        except Exception:
            pass

        # Strategy 3: Gemini format - parts array with thought=true flag
        # {"parts": [{"text": "...", "thought": true}, {"text": "..."}]}
        try:
            parts_array = msg.get("parts")
            if isinstance(parts_array, list):
                for part in parts_array:
                    if isinstance(part, dict) and part.get("thought") is True:
                        thought_text = part.get("text")
                        if thought_text and isinstance(thought_text, str) and thought_text.strip():
                            reasoning_parts.append(thought_text.strip())
        except Exception:
            pass

        # Strategy 4: Fallback - try other possible field names
        try:
            for field_name in ["chain_of_thought", "cot", "thinking", "thinking_log", "reasoning_log"]:
                field_value = msg.get(field_name)
                if field_value:
                    extracted = extract_text_from_message(field_value)
                    if extracted and extracted.strip():
                        reasoning_parts.append(extracted.strip())
                        break  # Only take first match from fallback fields
        except Exception:
            pass

        # Merge all reasoning segments
        if reasoning_parts:
            merged = "\n\n--- [Reasoning Section] ---\n\n".join(reasoning_parts)
            logger.debug(f"Reasoning content extracted: {len(merged)} chars from API response")
            return merged

        return ""

    except Exception as e:
        logger.warning(f"Failed to extract reasoning content from API response: {e}")
        return ""


def build_reasoning_snapshot(
    api_reasoning: str,
    strategy_text: Optional[str] = None,
    fallback_text: Optional[str] = None,
) -> str:
    """按三级优先级合并出 reasoning_snapshot。

    优先级：api_reasoning（推理模型思维链）> strategy_text（prompt 约定的策略说明）
    > fallback_text（兜底）。来源 ai_decision_service.py:2884-2905 的合并逻辑。

    Args:
        api_reasoning: extract_reasoning_content_safe 的返回值（最高优先级）。
        strategy_text: JSON 正文里的 trading_strategy / reasoning 字段（chat 模型主路径）。
        fallback_text: 早期提取的 reasoning_text 等兜底来源。

    Returns:
        合并后的快照文本（可能为空串）。
    """
    if api_reasoning and api_reasoning.strip():
        base = ""
        if isinstance(strategy_text, str) and strategy_text.strip():
            base = strategy_text.strip()
        if base:
            return f"{base}\n\n{api_reasoning.strip()}"
        return api_reasoning.strip()

    if isinstance(strategy_text, str) and strategy_text.strip():
        return strategy_text.strip()

    if fallback_text:
        return fallback_text
    return ""


# 兼容别名：保留与 ai_decision_service 原私有命名一致，便于原地替换 import 时最小改动
_extract_text_from_message = extract_text_from_message
_extract_reasoning_content_safe = extract_reasoning_content_safe


__all__ = [
    "extract_text_from_message",
    "extract_reasoning_content_safe",
    "build_reasoning_snapshot",
    # 兼容别名
    "_extract_text_from_message",
    "_extract_reasoning_content_safe",
]
