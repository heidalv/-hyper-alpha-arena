"""OpenCode Prompt 调用 trace — 轻量 JSONL（Langfuse 前置）。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.utils.jsonl_rotating import append_jsonl

logger = logging.getLogger(__name__)

TRACE_LOG = os.path.join("data", "prompt_trace.jsonl")
_lock = threading.Lock()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_version() -> str:
    try:
        from backend.services.prompt_registry import get_prompt_registry

        raw = get_prompt_registry()._manifest.get("raw") or {}
        return str(raw.get("version") or "1.0.0")
    except Exception:
        return "unknown"


def append_prompt_trace(
    *,
    task_id: str,
    consumer: str,
    ok: bool = True,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    entry: Dict[str, Any] = {
        "ts": _utc_iso(),
        "task_id": task_id,
        "consumer": consumer,
        "manifest_version": _manifest_version(),
        "ok": ok,
    }
    if error:
        entry["error"] = error[:500]
    if extra:
        entry["extra"] = extra
    try:
        append_jsonl(TRACE_LOG, entry)
    except Exception as exc:
        logger.debug("[PromptTrace] write failed: %s", exc)


def recent_prompt_traces(limit: int = 50) -> List[Dict[str, Any]]:
    if not os.path.isfile(TRACE_LOG):
        return []
    try:
        with open(TRACE_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-int(limit):]
        out: List[Dict[str, Any]] = []
        for ln in lines:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        return out
    except Exception as exc:
        logger.debug("[PromptTrace] read failed: %s", exc)
        return []
