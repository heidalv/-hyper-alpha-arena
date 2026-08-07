"""将后台日志告警推送到 Alpha 助手对话（去重、按严重等级）。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ERROR_ALERT_KIND = "error_alert"
ERROR_ALERT_MARKER = "<!-- error-alert -->"


def error_alert_fingerprint(badge: Dict[str, Any]) -> str:
    tops = badge.get("top_entries") or []
    raw = "|".join(
        f"{e.get('logger')}:{e.get('count')}:{e.get('severity_hint')}"
        for e in tops
        if isinstance(e, dict)
    )
    base = f"{badge.get('kind')}:{badge.get('count')}:{badge.get('total_errors')}:{raw}"
    return hashlib.sha256(base.encode("utf-8", errors="replace")).hexdigest()[:24]


def _severity_emoji(hint: str) -> str:
    return {"P0": "🚨", "P1": "⚠️", "P2": "ℹ️"}.get(hint, "ℹ️")


def format_entry_alert(entry: Dict[str, Any], *, badge_hint: str = "") -> str:
    hint = str(entry.get("severity_hint") or "P2").upper()
    logger_name = str(entry.get("logger") or "unknown")
    count = int(entry.get("count") or 0)
    sample = str(entry.get("sample") or "").strip()
    lines = [
        ERROR_ALERT_MARKER,
        f"**{_severity_emoji(hint)} 后台告警 · {hint}**",
        "",
        f"- 模块：`{logger_name}`",
        f"- 24h 出现：**{count}** 次",
    ]
    if sample:
        lines.append(f"- 摘要：{sample[:240]}")
    if badge_hint:
        lines.append("")
        lines.append(f"_{badge_hint}_")
    return "\n".join(lines)


def _last_pushed_fingerprint(db, conv_id: int) -> Optional[str]:
    from backend.database.models import AlphaAssistantMessage

    rows = (
        db.query(AlphaAssistantMessage)
        .filter(AlphaAssistantMessage.conversation_id == conv_id)
        .order_by(AlphaAssistantMessage.created_at.desc())
        .limit(40)
        .all()
    )
    for row in rows:
        if row.role != "assistant" or not row.tool_result_json:
            continue
        try:
            meta = json.loads(row.tool_result_json)
        except Exception:
            continue
        if meta.get("kind") == ERROR_ALERT_KIND and meta.get("batch_fp"):
            return str(meta["batch_fp"])
    return None


def sync_error_alerts_to_conversation(
    db,
    session_uuid: str,
    badge: Dict[str, Any],
    *,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """有新告警批次时写入助手对话（同一 fingerprint 不重复推送）。"""
    from backend.services.assistant_conversation_service import (
        append_message,
        get_conversation_by_uuid,
    )

    if not badge.get("count") or int(badge.get("total_errors") or 0) <= 0:
        return {"pushed": 0, "fingerprint": None, "skipped": "empty"}

    conv = get_conversation_by_uuid(db, session_uuid, user_id=user_id)
    if not conv:
        return {"pushed": 0, "fingerprint": None, "skipped": "no_conversation"}

    fp = error_alert_fingerprint(badge)
    if _last_pushed_fingerprint(db, conv.id) == fp:
        return {"pushed": 0, "fingerprint": fp, "skipped": "duplicate"}

    entries = [e for e in (badge.get("top_entries") or []) if isinstance(e, dict)]
    if not entries:
        return {"pushed": 0, "fingerprint": fp, "skipped": "no_entries"}

    hint = str(badge.get("hint") or "")
    pushed = 0
    for entry in entries[:5]:
        sev = str(entry.get("severity_hint") or "P2").upper()
        content = format_entry_alert(entry, badge_hint=hint if pushed == 0 else "")
        append_message(
            db,
            conv,
            role="assistant",
            content=content,
            tool_result={
                "kind": ERROR_ALERT_KIND,
                "severity": sev,
                "batch_fp": fp,
                "logger": entry.get("logger"),
                "count": entry.get("count"),
            },
            commit=False,
        )
        pushed += 1

    db.commit()
    logger.info(
        "[AlphaAssistant] 推送 %d 条告警到会话 %s (fp=%s)",
        pushed,
        session_uuid[:8],
        fp,
    )
    return {"pushed": pushed, "fingerprint": fp, "skipped": None}
