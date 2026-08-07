"""
通知配置 API 路由

提供飞书通知的配置管理、测试发送和状态查询接口。
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notification", tags=["notification"])


# ─── 请求模型 ───

class NotifyConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    webhook_url: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_chat_id: Optional[str] = None
    min_level: Optional[str] = None
    enable_open: Optional[bool] = None
    enable_close: Optional[bool] = None
    enable_tp_sl: Optional[bool] = None
    enable_liquidation: Optional[bool] = None
    enable_system: Optional[bool] = None
    min_interval_seconds: Optional[int] = None
    feishu_assistant_enabled: Optional[bool] = None
    assistant_notify_actions: Optional[bool] = None
    assistant_notify_p0: Optional[bool] = None
    assistant_daily_report_enabled: Optional[bool] = None


class TestMessageRequest(BaseModel):
    message: str = "Alpha Arena 通知测试消息"


# ─── 路由 ───

@router.get("/config")
async def get_notification_config():
    """获取当前通知配置（敏感字段脱敏）"""
    from backend.services.openclaw_notify import get_notifier
    n = get_notifier()
    return {"ok": True, "config": n.get_config()}


@router.post("/config")
async def update_notification_config(body: NotifyConfigUpdate):
    """更新通知配置"""
    from backend.services.openclaw_notify import get_notifier
    n = get_notifier()
    patch = {k: v for k, v in body.dict().items() if v is not None}
    if not patch:
        raise HTTPException(400, "没有提供任何配置项")
    n.save_config(patch)
    return {"ok": True, "config": n.get_config()}


@router.post("/webhook-config")
async def save_webhook_config(body: Dict[str, Any]):
    """兼容旧版 webhook 配置接口"""
    from backend.services.openclaw_notify import get_notifier
    n = get_notifier()
    patch: Dict[str, Any] = {}
    if "webhook_url" in body:
        patch["webhook_url"] = body["webhook_url"]
        patch["enabled"] = True
    if "enable_critical" in body:
        patch["enable_liquidation"] = body["enable_critical"]
    if patch:
        n.save_config(patch)
    return {"ok": True}


@router.post("/test")
async def test_notification(body: TestMessageRequest):
    """发送测试消息验证连通性"""
    from backend.services.openclaw_notify import get_notifier
    n = get_notifier()
    result = await n.test_connection()

    any_ok = any(ch.get("ok") for ch in result.values())
    return {
        "ok": any_ok,
        "channels": result,
        "message": "至少一个通知渠道可用" if any_ok else "所有渠道均不可用，请检查配置",
    }


@router.get("/status")
async def notification_status():
    """检查通知服务状态"""
    from backend.services.openclaw_notify import get_notifier
    n = get_notifier()
    cfg = n.get_config()

    webhook_configured = bool(cfg.get("webhook_url"))
    app_configured = bool(cfg.get("feishu_app_id")) and cfg.get("feishu_app_id") != ""

    return {
        "ok": True,
        "enabled": cfg.get("enabled", False),
        "channels": {
            "webhook": {"configured": webhook_configured},
            "feishu_app": {"configured": app_configured},
        },
    }
