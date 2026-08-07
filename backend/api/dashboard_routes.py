"""交易矩阵仪表盘 API — /api/dashboard/*

统一账户概览聚合（多账户 x 多交易所 x 多模式）+ 布局持久化。
概览部分为纯只读聚合，不触碰任何下单/资金逻辑；复用 dashboard_aggregator 的编排结果。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import DashboardLayout
from backend.services.dashboard_aggregator import get_accounts_overview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# 当前系统以单用户为主（与 bootstrap 默认账户模式一致），布局暂不做多用户隔离；
# 预留 user_id 字段以便未来引入多用户体系时无需迁移 schema。
_DEFAULT_USER_ID: Optional[int] = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AccountSelector(BaseModel):
    account_id: int
    exchange: str = "hyperliquid"
    trading_mode: str = Field("paper", pattern="^(paper|testnet|mainnet)$")


class OverviewRequestBody(BaseModel):
    selections: List[AccountSelector]


@router.post("/overview")
def post_overview(body: OverviewRequestBody, db: Session = Depends(get_db)):
    """批量聚合多个「账户 x 交易所 x 模式」组合的统一概览（多选对比模式）。"""
    if not body.selections:
        return {"generated_at": None, "accounts": []}

    selections = [sel.model_dump() for sel in body.selections]
    try:
        results = get_accounts_overview(db, selections)
    except Exception as exc:
        logger.error(f"[dashboard_routes] overview aggregation failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"聚合失败: {exc}")

    from datetime import datetime, timezone
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "accounts": results,
    }


# ─────────────────────────────────────────────
#  布局持久化 CRUD — /api/dashboard/layouts*
# ─────────────────────────────────────────────

class WidgetConfig(BaseModel):
    id: str
    type: str
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 4
    config: Dict[str, Any] = Field(default_factory=dict)


class LayoutCreateBody(BaseModel):
    name: str = "默认布局"
    widgets: List[WidgetConfig] = Field(default_factory=list)
    selected_accounts: List[AccountSelector] = Field(default_factory=list)
    activate: bool = False


class LayoutUpdateBody(BaseModel):
    name: Optional[str] = None
    widgets: Optional[List[WidgetConfig]] = None
    selected_accounts: Optional[List[AccountSelector]] = None


def _layout_to_dict(layout: DashboardLayout) -> Dict[str, Any]:
    return {
        "id": layout.id,
        "name": layout.name,
        "is_active": bool(layout.is_active),
        "widgets": layout.widgets or [],
        "selected_accounts": layout.selected_accounts or [],
        "created_at": layout.created_at.isoformat() if layout.created_at else None,
        "updated_at": layout.updated_at.isoformat() if layout.updated_at else None,
    }


@router.get("/layouts")
def list_layouts(db: Session = Depends(get_db)):
    """列出当前用户的全部已保存布局。"""
    rows = (
        db.query(DashboardLayout)
        .filter(DashboardLayout.user_id == _DEFAULT_USER_ID)
        .order_by(DashboardLayout.updated_at.desc())
        .all()
    )
    return {"layouts": [_layout_to_dict(r) for r in rows]}


@router.get("/layouts/active")
def get_active_layout(db: Session = Depends(get_db)):
    """获取当前激活布局；若从未保存过，返回 null（前端使用内置默认布局）。"""
    row = (
        db.query(DashboardLayout)
        .filter(DashboardLayout.user_id == _DEFAULT_USER_ID, DashboardLayout.is_active == True)  # noqa: E712
        .first()
    )
    return {"layout": _layout_to_dict(row) if row else None}


@router.post("/layouts")
def create_layout(body: LayoutCreateBody, db: Session = Depends(get_db)):
    """新建一个命名布局；activate=True 时立即置为当前激活布局。"""
    if body.activate:
        db.query(DashboardLayout).filter(
            DashboardLayout.user_id == _DEFAULT_USER_ID, DashboardLayout.is_active == True  # noqa: E712
        ).update({"is_active": False})

    layout = DashboardLayout(
        user_id=_DEFAULT_USER_ID,
        name=body.name,
        is_active=body.activate,
        widgets=[w.model_dump() for w in body.widgets],
        selected_accounts=[s.model_dump() for s in body.selected_accounts],
    )
    db.add(layout)
    db.commit()
    db.refresh(layout)
    return {"layout": _layout_to_dict(layout)}


@router.put("/layouts/{layout_id}")
def update_layout(layout_id: int, body: LayoutUpdateBody, db: Session = Depends(get_db)):
    """更新布局（拖拽/缩放变更、重命名、重选账户组合）。"""
    layout = (
        db.query(DashboardLayout)
        .filter(DashboardLayout.id == layout_id, DashboardLayout.user_id == _DEFAULT_USER_ID)
        .first()
    )
    if not layout:
        raise HTTPException(status_code=404, detail="layout not found")

    if body.name is not None:
        layout.name = body.name
    if body.widgets is not None:
        layout.widgets = [w.model_dump() for w in body.widgets]
    if body.selected_accounts is not None:
        layout.selected_accounts = [s.model_dump() for s in body.selected_accounts]

    db.commit()
    db.refresh(layout)
    return {"layout": _layout_to_dict(layout)}


@router.delete("/layouts/{layout_id}")
def delete_layout(layout_id: int, db: Session = Depends(get_db)):
    layout = (
        db.query(DashboardLayout)
        .filter(DashboardLayout.id == layout_id, DashboardLayout.user_id == _DEFAULT_USER_ID)
        .first()
    )
    if not layout:
        raise HTTPException(status_code=404, detail="layout not found")
    db.delete(layout)
    db.commit()
    return {"status": "deleted", "id": layout_id}


@router.post("/layouts/{layout_id}/activate")
def activate_layout(layout_id: int, db: Session = Depends(get_db)):
    """将指定布局设为当前激活布局（同用户下唯一激活）。"""
    layout = (
        db.query(DashboardLayout)
        .filter(DashboardLayout.id == layout_id, DashboardLayout.user_id == _DEFAULT_USER_ID)
        .first()
    )
    if not layout:
        raise HTTPException(status_code=404, detail="layout not found")

    db.query(DashboardLayout).filter(
        DashboardLayout.user_id == _DEFAULT_USER_ID, DashboardLayout.is_active == True  # noqa: E712
    ).update({"is_active": False})
    layout.is_active = True
    db.commit()
    db.refresh(layout)
    return {"layout": _layout_to_dict(layout)}
