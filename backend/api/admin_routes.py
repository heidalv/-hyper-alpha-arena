# backend/api/admin_routes.py
"""阶段4 Task 4.3: 管理后台 API —— ``/api/admin/*``。

能力
----
  - ``GET    /api/admin/users``              列出全部用户(跨租户)。
  - ``PATCH  /api/admin/users/{id}/tier``    调整用户等级(free/pro/vip)。
  - ``PATCH  /api/admin/users/{id}/role``    调整角色(user/admin)。
  - ``PATCH  /api/admin/users/{id}/status``  启用/停用用户(防自停)。
  - ``GET    /api/admin/audit-logs``         查看管理员操作审计日志。
  - ``GET    /api/admin/stats``              平台统计(总用户数 / 按 tier 分布)。

授权
----
整个路由组挂 ``dependencies=[Depends(require_admin)]``:
  - 中间件先校验 JWT 并注入 ``scope["state"]["role"]``;
  - ``require_admin`` 再读 state.role,非 admin → 403。
两层独立(中间件管"你是谁",依赖管"你能不能"),互为兜底。

跨租户可见性
-----------
``users`` 表是 GLOBAL 表(无 tenant_id),``db.query(User).all()`` 天然返回全部用户。
对租户隔离表(如 accounts/llm_configurations),admin 请求经中间件设的
``app.is_admin='on'`` GUC 会短路 RLS policy(Task 4.2),admin 同样跨租户可见。

审计
----
所有写操作(改 tier / 改 status)都向 ``admin_audit_logs`` 写一条记录,
``detail`` 用 JSON 存变更前/后值,便于事后追溯。读操作(list/audit-logs/stats)
不记审计(只读不产生副作用,审计表本身已记录了所有写)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.core.permissions import require_admin
from backend.database.connection import get_db
from backend.database.models import AdminAuditLog, User

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


class TierUpdate(BaseModel):
    tier: str  # free/pro/vip


class RoleUpdate(BaseModel):
    role: str  # user/admin


class StatusUpdate(BaseModel):
    is_active: str  # "true"/"false" —— 与 User.is_active 存储风格一致(String)


def _admin_id_from_state(request: Request) -> int:
    """从 scope['state'] 取 admin user_id(已由 require_admin 校验过,必定存在)。

    ``require_admin`` 作为组级依赖已保证 role=='admin' 且 user_id 存在;这里只是
    把它取出来用于审计日志。用 int() 强转:中间件写入的 user_id 是 JWT 的 sub(字符串)。
    """
    return int(request.scope["state"]["user_id"])


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """列出所有用户(跨租户;users 是 GLOBAL 表,admin RLS 穿透亦生效)。"""
    rows = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "tier": u.tier,
            "role": u.role,
            "is_active": u.is_active,
        }
        for u in rows
    ]


@router.patch("/users/{user_id}/tier")
def set_user_tier(
    user_id: int,
    body: TierUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """调整用户等级,写审计日志。"""
    admin_id = _admin_id_from_state(request)
    tier = (body.tier or "").strip().lower()
    if tier not in ("free", "pro", "vip"):
        raise HTTPException(status_code=400, detail="tier must be free, pro or vip")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    old = u.tier
    u.tier = tier
    db.add(
        AdminAuditLog(
            admin_user_id=admin_id,
            action="set_tier",
            target_user_id=user_id,
            detail={"old": old, "new": tier},
        )
    )
    db.commit()
    return {"id": u.id, "tier": u.tier}


@router.patch("/users/{user_id}/role")
def set_user_role(
    user_id: int,
    body: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """调整用户角色(user/admin),写审计日志。禁止把自己降为非 admin(防锁死)。"""
    admin_id = _admin_id_from_state(request)
    role = (body.role or "").strip().lower()
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role must be user or admin")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    if user_id == admin_id and role != "admin":
        raise HTTPException(status_code=400, detail="cannot demote yourself")
    old = u.role
    u.role = role
    db.add(
        AdminAuditLog(
            admin_user_id=admin_id,
            action="set_role",
            target_user_id=user_id,
            detail={"old": old, "new": role},
        )
    )
    db.commit()
    return {"id": u.id, "role": u.role}


@router.patch("/users/{user_id}/status")
def set_user_status(
    user_id: int,
    body: StatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """启用/停用用户,写审计日志。禁止 admin 停用自己(防把自己锁死)。"""
    admin_id = _admin_id_from_state(request)
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    # 防止 admin 停用自己:否则会把自己锁出系统(无人能再恢复)。
    if user_id == admin_id:
        raise HTTPException(status_code=400, detail="cannot disable yourself")
    u.is_active = body.is_active
    db.add(
        AdminAuditLog(
            admin_user_id=admin_id,
            action="set_status",
            target_user_id=user_id,
            detail={"is_active": body.is_active},
        )
    )
    db.commit()
    return {"id": u.id, "is_active": u.is_active}


@router.get("/audit-logs")
def audit_logs(limit: int = 100, db: Session = Depends(get_db)):
    """查看管理员操作审计日志(倒序,默认最近 100 条)。"""
    rows = (
        db.query(AdminAuditLog)
        .order_by(AdminAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "admin_user_id": r.admin_user_id,
            "action": r.action,
            "target_user_id": r.target_user_id,
            "detail": r.detail,
            "created_at": str(r.created_at) if r.created_at is not None else None,
        }
        for r in rows
    ]


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """平台统计:总用户数 + 按 tier 分布。

    ``db.query(User.tier, func.count(User.id)).group_by(User.tier)`` 返回
    ``[(tier, count), ...]``,转成 dict 给前端。
    """
    total = db.query(User).count()
    tier_rows = (
        db.query(User.tier, func.count(User.id)).group_by(User.tier).all()
    )
    by_tier = {tier: cnt for tier, cnt in tier_rows}
    return {"total_users": total, "by_tier": by_tier}
