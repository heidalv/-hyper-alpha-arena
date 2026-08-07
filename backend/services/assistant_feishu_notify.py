"""Alpha 助手飞书推送 — 日报 / 审计 / P0 告警。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _assistant_notify_enabled() -> bool:
    try:
        from backend.config.settings import FEISHU_ASSISTANT_ENABLED
        from backend.services.openclaw_notify import get_notifier

        if not FEISHU_ASSISTANT_ENABLED:
            return False
        cfg = get_notifier().get_config()
        return bool(cfg.get("enabled")) and bool(cfg.get("feishu_assistant_enabled"))
    except Exception:
        return False


def push_text_to_feishu(text: str, *, title: str = "Alpha 助手") -> Dict[str, Any]:
    if not _assistant_notify_enabled():
        return {"ok": False, "reason": "disabled"}
    from backend.services.openclaw_notify import get_notifier

    ok = get_notifier().send_sync(title=title, text=text, event_type="system", level="info")
    return {"ok": ok}


def push_assistant_daily_report() -> Dict[str, Any]:
    from backend.services.assistant_daily_report_service import build_daily_report_payload

    payload = build_daily_report_payload()
    return push_text_to_feishu(payload["text"], title=payload["title"])


def notify_assistant_action(
    *,
    user_action: str,
    ok: bool,
    session_id: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    if not _assistant_notify_enabled():
        return
    from backend.services.openclaw_notify import get_notifier

    cfg = get_notifier().get_config()
    if not cfg.get("assistant_notify_actions", True):
        return

    status = "成功" if ok else "失败"
    lines = [f"**Alpha 助手操作** — {user_action} {status}"]
    if session_id:
        lines.append(f"会话: `{session_id[:8]}…`")
    if result:
        brief = ", ".join(f"{k}={v}" for k, v in list(result.items())[:4])
        if brief:
            lines.append(brief)
    if error:
        lines.append(f"错误: {error[:200]}")
    get_notifier().send_sync(title="Alpha 助手", text="\n".join(lines), event_type="system")


def notify_p0_errors_if_needed(*, p0_count: int, hint: str) -> None:
    if p0_count <= 0 or not _assistant_notify_enabled():
        return
    from backend.services.openclaw_notify import get_notifier

    cfg = get_notifier().get_config()
    if not cfg.get("assistant_notify_p0", True):
        return
    get_notifier().send_sync(
        title="Alpha 助手 · 严重错误",
        text=f"检测到 **{p0_count}** 类 P0 错误\n{hint}",
        event_type="system",
        level="critical",
    )
