"""钉钉推送API路由 — Phase 2 存根（钉钉通知已废弃）"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/api/dingtalk", tags=["dingtalk"])

_REMOVED = {"error": "钉钉通知模块已在 Phase 2 移除", "code": "FEATURE_REMOVED"}


@router.get("/{path:path}")
@router.post("/{path:path}")
@router.put("/{path:path}")
@router.delete("/{path:path}")
async def dingtalk_stub(path: str):
    return JSONResponse(status_code=410, content=_REMOVED)
