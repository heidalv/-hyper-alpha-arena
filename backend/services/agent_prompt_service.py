"""Agent Prompt 渲染 — PromptRegistry + trace + 内联 fallback。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def render_agent_task(
    task_id: str,
    variables: Dict[str, Any],
    *,
    consumer: str,
    fallback_text: Optional[str] = None,
) -> str:
    """渲染 Agent task prompt；失败时回退 fallback_text（必填）。"""
    try:
        from backend.services.prompt_registry import get_prompt_registry
        text = get_prompt_registry().render_task(task_id, _normalize_vars(variables), consumer=consumer)
        extra: Dict[str, Any] = {}
        try:
            from backend.services.prompt_l2_resolver import get_last_resolution
            res = get_last_resolution(task_id)
            if res:
                extra = res.to_dict()
        except Exception:
            pass
        _trace_prompt(task_id, consumer, ok=True, extra=extra or None)
        return text
    except Exception as exc:
        logger.debug("[AgentPrompt] registry 渲染失败 task=%s: %s", task_id, exc)
        _trace_prompt(task_id, consumer, ok=False, error=str(exc))
        if fallback_text:
            return fallback_text
        raise


def _normalize_vars(variables: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in (variables or {}).items():
        if isinstance(v, dict):
            out[k] = json.dumps(v, ensure_ascii=False, indent=2)
        else:
            out[k] = v if v is not None else ""
    return out


def _trace_prompt(task_id: str, consumer: str, *, ok: bool, error: str = "",
                  extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        from backend.services.prompt_trace_service import append_prompt_trace
        append_prompt_trace(
            task_id=task_id,
            consumer=consumer,
            ok=ok,
            error=error or None,
            extra=extra or None,
        )
    except Exception:
        pass
