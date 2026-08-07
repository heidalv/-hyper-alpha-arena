"""Alpha 助手 L2/L3 操作审计 — append-only JSONL。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUDIT_LOG = os.path.join("data", "assistant_audit.jsonl")
_lock = threading.Lock()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_assistant_audit(
    *,
    user_action: str,
    args: Optional[Dict[str, Any]] = None,
    ok: bool = True,
    session_id: Optional[str] = None,
    level: str = "L2",
    error: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    rollback_hint: Optional[str] = None,
) -> None:
    """记录助手触发的写操作（paper pace / 提案 apply / live 晋升等）。"""
    entry: Dict[str, Any] = {
        "ts": _utc_iso(),
        "user_action": user_action,
        "level": level,
        "args": args or {},
        "ok": ok,
    }
    if session_id:
        entry["session_id"] = session_id
    if error:
        entry["error"] = error[:500]
    if result is not None:
        entry["result"] = result
    if rollback_hint:
        entry["rollback_hint"] = rollback_hint

    try:
        os.makedirs(os.path.dirname(AUDIT_LOG) or ".", exist_ok=True)
        with _lock:
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[AssistantAudit] write failed: %s", exc)
        return

    try:
        from backend.services.assistant_feishu_notify import notify_assistant_action

        notify_assistant_action(
            user_action=user_action,
            ok=ok,
            session_id=session_id,
            result=result,
            error=error,
        )
    except Exception:
        pass


def recent_assistant_audit(limit: int = 50) -> List[Dict[str, Any]]:
    if not os.path.isfile(AUDIT_LOG):
        return []
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-int(limit):]
        out: List[Dict[str, Any]] = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except Exception as exc:
        logger.debug("[AssistantAudit] read failed: %s", exc)
        return []
