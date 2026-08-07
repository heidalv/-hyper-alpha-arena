"""可视化策略路由 — Phase 2 存根（可视化策略设计器已废弃）"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_REMOVED_MSG = {"error": "可视化策略设计器已在 Phase 2 移除", "code": "FEATURE_REMOVED"}


@router.get("/atas/v2/strategies/{path:path}")
@router.post("/atas/v2/strategies/{path:path}")
@router.put("/atas/v2/strategies/{path:path}")
@router.delete("/atas/v2/strategies/{path:path}")
async def visual_strategy_stub(path: str):
    return JSONResponse(status_code=410, content=_REMOVED_MSG)
