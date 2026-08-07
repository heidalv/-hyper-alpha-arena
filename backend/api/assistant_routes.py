"""Alpha 助手 API。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.database.connection import get_db

router = APIRouter(prefix="/api/assistant", tags=["Alpha Assistant"])


class AssistantChatRequest(BaseModel):
    user_message: str = Field(..., min_length=1, max_length=4000)
    session_id: Optional[str] = None
    page_context: Optional[Dict[str, Any]] = None


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    seed_welcome: bool = True


@router.get("/badge")
def assistant_badge(
    window_hours: int = Query(24, ge=1, le=168),
    session_uuid: Optional[str] = Query(None, description="传入则把新告警推入该对话"),
    db=Depends(get_db),
) -> Dict[str, Any]:
    from backend.services.assistant_badge_service import build_assistant_badge
    from backend.services.assistant_error_alert_service import sync_error_alerts_to_conversation

    badge = build_assistant_badge(window_hours=window_hours)
    badge["pushed_alerts"] = 0
    badge["alert_fingerprint"] = None
    if session_uuid and badge.get("count"):
        sync = sync_error_alerts_to_conversation(db, session_uuid, badge)
        badge["pushed_alerts"] = int(sync.get("pushed") or 0)
        badge["alert_fingerprint"] = sync.get("fingerprint")
    return badge


@router.get("/conversations")
def list_assistant_conversations(
    db=Depends(get_db),
    limit: int = Query(30, ge=1, le=100),
    channel: Optional[str] = Query(None, description="web | feishu"),
) -> Dict[str, Any]:
    from backend.services.assistant_conversation_service import list_conversations

    return {"conversations": list_conversations(db, channel=channel, limit=limit)}


@router.post("/conversations")
def create_assistant_conversation(
    body: CreateConversationRequest,
    db=Depends(get_db),
) -> Dict[str, Any]:
    from backend.services.assistant_conversation_service import create_conversation

    conv = create_conversation(
        db,
        title=body.title,
        seed_welcome=body.seed_welcome,
    )
    return {
        "session_uuid": conv.session_uuid,
        "title": conv.title,
        "channel": conv.channel,
    }


@router.get("/conversations/{session_uuid}/messages")
def get_assistant_messages(session_uuid: str, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.assistant_conversation_service import get_messages_for_conversation

    messages = get_messages_for_conversation(db, session_uuid)
    if messages is None:
        raise HTTPException(404, "Conversation not found")
    return {"messages": messages, "session_uuid": session_uuid}


@router.delete("/conversations/{session_uuid}")
def delete_assistant_conversation(session_uuid: str, db=Depends(get_db)) -> Dict[str, Any]:
    from backend.services.assistant_conversation_service import delete_conversation

    ok = delete_conversation(db, session_uuid)
    if not ok:
        raise HTTPException(404, "Conversation not found")
    return {"deleted": True, "session_uuid": session_uuid}


@router.post("/daily-report/push")
def push_daily_report() -> Dict[str, Any]:
    """手动推送日报到飞书（需已配置通知）。"""
    from backend.services.assistant_feishu_notify import push_assistant_daily_report

    return push_assistant_daily_report()


@router.post("/chat-stream")
def assistant_chat_stream(body: AssistantChatRequest):
    from backend.services.alpha_assistant_service import chat_stream

    def event_generator():
        yield from chat_stream(
            user_message=body.user_message,
            session_id=body.session_id,
            page_context=body.page_context,
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/audit")
def assistant_audit(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    from backend.services.assistant_audit_service import recent_assistant_audit

    entries = recent_assistant_audit(limit=limit)
    return {"entries": entries, "count": len(entries)}
