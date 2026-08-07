"""飞书事件订阅 — Alpha 助手双向对话。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feishu", tags=["Feishu Assistant"])


@router.post("/events")
async def feishu_events(request: Request) -> Dict[str, Any]:
    """
    飞书开放平台事件回调。
    - URL 验证：返回 challenge
    - im.message.receive_v1：转发 Alpha 助手
    """
    try:
        body = await request.json()
    except Exception:
        return {"code": 1, "msg": "invalid json"}

    # URL 验证（1.0 / 2.0）
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    schema = body.get("schema")
    if schema == "2.0":
        header = body.get("header") or {}
        if header.get("event_type") == "url_verification":
            return {"challenge": body.get("challenge", "")}

    from backend.config.settings import FEISHU_VERIFICATION_TOKEN

    token = body.get("token") or (body.get("header") or {}).get("token")
    if FEISHU_VERIFICATION_TOKEN and token and token != FEISHU_VERIFICATION_TOKEN:
        logger.warning("[FeishuEvents] token mismatch")
        return {"code": 1, "msg": "invalid token"}

    from backend.services.feishu_assistant_bridge import handle_feishu_message

    action, result = handle_feishu_message(body)
    logger.info("[FeishuEvents] action=%s result=%s", action, json.dumps(result or {}, ensure_ascii=False)[:200])
    return {"code": 0, "msg": "ok", "action": action}


@router.get("/events/url")
def feishu_callback_url_hint(request: Request) -> Dict[str, Any]:
    """返回飞书开放平台应配置的回调 URL。"""
    base = str(request.base_url).rstrip("/")
    return {
        "callback_url": f"{base}/api/feishu/events",
        "note": "在飞书开放平台 → 事件订阅 → 请求地址 填入上述 URL，并订阅 im.message.receive_v1",
    }
