"""飞书 ↔ Alpha 助手桥接。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"@\S+\s*")


def strip_feishu_mentions(text: str) -> str:
    return _MENTION_RE.sub("", (text or "").strip()).strip()


def parse_message_text(content_raw: Any) -> str:
    if not content_raw:
        return ""
    if isinstance(content_raw, dict):
        return str(content_raw.get("text") or content_raw.get("content") or "")
    if isinstance(content_raw, str):
        try:
            obj = json.loads(content_raw)
            if isinstance(obj, dict):
                return str(obj.get("text") or "")
        except json.JSONDecodeError:
            return content_raw.strip()
    return str(content_raw).strip()


def extract_im_message(event: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """从飞书 im.message.receive_v1 事件提取字段。"""
    msg = event.get("message") or {}
    if not msg:
        return None
    chat_id = str(msg.get("chat_id") or "")
    message_id = str(msg.get("message_id") or "")
    msg_type = str(msg.get("message_type") or "text")
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    open_id = str(sender_id.get("open_id") or sender.get("open_id") or "")
    text = parse_message_text(msg.get("content"))
    text = strip_feishu_mentions(text)
    if not chat_id or not text:
        return None
    if msg_type != "text":
        return {"chat_id": chat_id, "open_id": open_id, "message_id": message_id, "text": "", "skip": "unsupported_type"}
    return {
        "chat_id": chat_id,
        "open_id": open_id,
        "message_id": message_id,
        "text": text,
    }


def handle_feishu_message(payload: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    处理飞书消息事件。
    返回 (action, result) — action: ignore | reply | error
    """
    from backend.config.settings import FEISHU_ASSISTANT_ENABLED
    from backend.services.openclaw_notify import get_notifier

    if not FEISHU_ASSISTANT_ENABLED:
        return "ignore", {"reason": "assistant_disabled"}

    cfg = get_notifier().get_config()
    if not cfg.get("feishu_assistant_enabled"):
        return "ignore", {"reason": "feishu_assistant_not_enabled_in_config"}

    header = payload.get("header") or {}
    event_type = header.get("event_type") or ""
    event = payload.get("event") or {}

    if event_type == "im.message.receive_v1":
        parsed = extract_im_message(event)
    elif payload.get("type") == "event_callback":
        inner = event if event else payload.get("event") or {}
        parsed = extract_im_message(inner)
    else:
        return "ignore", {"reason": "not_message_event", "event_type": event_type or payload.get("type")}

    if not parsed:
        return "ignore", {"reason": "no_message"}
    if parsed.get("skip"):
        reply = "暂仅支持文本消息，请直接 @机器人 提问。"
        get_notifier().send_sync_text_to_chat(parsed["chat_id"], reply)
        return "reply", {"text": reply}

    text = parsed["text"]
    if not text:
        return "ignore", {"reason": "empty_text"}

    from backend.services.alpha_assistant_service import chat_sync

    try:
        result = chat_sync(
            user_message=text,
            channel="feishu",
            feishu_chat_id=parsed["chat_id"],
            feishu_open_id=parsed["open_id"],
        )
        reply = result.get("content") or "（无回复）"
        get_notifier().send_sync_text_to_chat(parsed["chat_id"], reply, title="Alpha 助手")
        return "reply", {"session_id": result.get("session_id"), "text_len": len(reply)}
    except Exception as exc:
        logger.error("[FeishuBridge] chat failed: %s", exc, exc_info=True)
        err_text = f"助手处理失败：{exc}"
        get_notifier().send_sync_text_to_chat(parsed["chat_id"], err_text)
        return "error", {"error": str(exc)}
