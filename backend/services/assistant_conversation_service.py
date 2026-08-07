"""Alpha 助手会话持久化 — DB CRUD。"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.database.models import (
    AlphaAssistantConversation,
    AlphaAssistantMessage,
    User,
)

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "新对话"
WELCOME_MESSAGE = "你好，我是 Alpha 助手。可以问我：今天有什么报错？OpenCode 在线吗？"


def get_default_user_id(db: Session) -> int:
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise RuntimeError("default user not found")
    return int(user.id)


def auto_title_from_first_user_msg(text: str, *, max_len: int = 40) -> str:
    t = (text or "").strip().replace("\n", " ")
    if not t:
        return DEFAULT_TITLE
    return t[:max_len] + ("…" if len(t) > max_len else "")


def create_conversation(
    db: Session,
    *,
    user_id: Optional[int] = None,
    channel: str = "web",
    session_uuid: Optional[str] = None,
    title: Optional[str] = None,
    feishu_chat_id: Optional[str] = None,
    feishu_open_id: Optional[str] = None,
    seed_welcome: bool = False,
) -> AlphaAssistantConversation:
    uid = user_id if user_id is not None else get_default_user_id(db)
    sid = session_uuid or str(uuid.uuid4())
    row = AlphaAssistantConversation(
        session_uuid=sid,
        user_id=uid,
        title=title or DEFAULT_TITLE,
        channel=channel,
        feishu_chat_id=feishu_chat_id,
        feishu_open_id=feishu_open_id,
    )
    db.add(row)
    db.flush()
    if seed_welcome:
        append_message(db, row, role="assistant", content=WELCOME_MESSAGE)
    db.commit()
    db.refresh(row)
    return row


def resolve_or_create_conversation(
    db: Session,
    *,
    session_uuid: Optional[str] = None,
    user_id: Optional[int] = None,
    channel: str = "web",
    feishu_chat_id: Optional[str] = None,
    feishu_open_id: Optional[str] = None,
) -> AlphaAssistantConversation:
    uid = user_id if user_id is not None else get_default_user_id(db)

    if session_uuid:
        row = (
            db.query(AlphaAssistantConversation)
            .filter(
                AlphaAssistantConversation.session_uuid == session_uuid,
                AlphaAssistantConversation.user_id == uid,
            )
            .first()
        )
        if row:
            return row

    if channel == "feishu" and feishu_chat_id and feishu_open_id:
        row = (
            db.query(AlphaAssistantConversation)
            .filter(
                AlphaAssistantConversation.channel == "feishu",
                AlphaAssistantConversation.feishu_chat_id == feishu_chat_id,
                AlphaAssistantConversation.feishu_open_id == feishu_open_id,
                AlphaAssistantConversation.user_id == uid,
            )
            .order_by(AlphaAssistantConversation.updated_at.desc())
            .first()
        )
        if row:
            return row

    return create_conversation(
        db,
        user_id=uid,
        channel=channel,
        session_uuid=session_uuid,
        feishu_chat_id=feishu_chat_id,
        feishu_open_id=feishu_open_id,
    )


def list_conversations(
    db: Session,
    *,
    user_id: Optional[int] = None,
    channel: Optional[str] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    uid = user_id if user_id is not None else get_default_user_id(db)
    q = db.query(AlphaAssistantConversation).filter(AlphaAssistantConversation.user_id == uid)
    if channel:
        q = q.filter(AlphaAssistantConversation.channel == channel)
    rows = q.order_by(AlphaAssistantConversation.updated_at.desc()).limit(limit).all()
    out: List[Dict[str, Any]] = []
    for row in rows:
        msg_count = (
            db.query(AlphaAssistantMessage)
            .filter(AlphaAssistantMessage.conversation_id == row.id)
            .count()
        )
        out.append(
            {
                "session_uuid": row.session_uuid,
                "title": row.title,
                "channel": row.channel,
                "messageCount": msg_count,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return out


def get_conversation_by_uuid(
    db: Session,
    session_uuid: str,
    *,
    user_id: Optional[int] = None,
) -> Optional[AlphaAssistantConversation]:
    uid = user_id if user_id is not None else get_default_user_id(db)
    return (
        db.query(AlphaAssistantConversation)
        .filter(
            AlphaAssistantConversation.session_uuid == session_uuid,
            AlphaAssistantConversation.user_id == uid,
        )
        .first()
    )


def get_messages_for_conversation(
    db: Session,
    session_uuid: str,
    *,
    user_id: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    conv = get_conversation_by_uuid(db, session_uuid, user_id=user_id)
    if not conv:
        return None
    rows = (
        db.query(AlphaAssistantMessage)
        .filter(AlphaAssistantMessage.conversation_id == conv.id)
        .order_by(AlphaAssistantMessage.created_at)
        .all()
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "createdAt": m.created_at.isoformat() if m.created_at else None,
            **_message_alert_meta(m),
        }
        for m in rows
    ]


def _message_alert_meta(m: AlphaAssistantMessage) -> Dict[str, Any]:
    if not m.tool_result_json:
        return {}
    try:
        meta = json.loads(m.tool_result_json)
    except Exception:
        return {}
    if meta.get("kind") != "error_alert":
        return {}
    sev = str(meta.get("severity") or "P2").upper()
    return {
        "isErrorAlert": True,
        "alertSeverity": sev,
    }


def append_message(
    db: Session,
    conv: AlphaAssistantConversation,
    *,
    role: str,
    content: str,
    tool_result: Optional[Dict[str, Any]] = None,
    commit: bool = True,
) -> AlphaAssistantMessage:
    msg = AlphaAssistantMessage(
        conversation_id=conv.id,
        role=role,
        content=content,
        tool_result_json=json.dumps(tool_result, ensure_ascii=False) if tool_result else None,
    )
    db.add(msg)
    if role == "user" and conv.title == DEFAULT_TITLE:
        conv.title = auto_title_from_first_user_msg(content)
    db.flush()
    if commit:
        db.commit()
        db.refresh(msg)
    return msg


def delete_conversation(
    db: Session,
    session_uuid: str,
    *,
    user_id: Optional[int] = None,
) -> bool:
    conv = get_conversation_by_uuid(db, session_uuid, user_id=user_id)
    if not conv:
        return False
    db.delete(conv)
    db.commit()
    return True
