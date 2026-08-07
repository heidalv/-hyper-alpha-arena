"""HTTP 请求里取当前登录用户 / 租户（JWT 注入的 request.state）。

多租户约定：``tenant_id == users.id``。普通用户只能碰自己的 LLM / 交易所密钥 /
发现因子；平台只共享「基础因子」代码库，不共享密钥与挖掘产物。
"""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import HTTPException, Request


def current_user_id(request: Request, *, required: bool = True) -> Optional[int]:
    """从中间件注入的 state 读 user_id。"""
    state = request.scope.get("state", {}) or {}
    raw = state.get("user_id")
    if raw is None:
        if required:
            raise HTTPException(status_code=401, detail="Authentication required")
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        if required:
            raise HTTPException(status_code=401, detail="Authentication required")
        return None


def current_tenant_id(request: Request, *, required: bool = True) -> Optional[int]:
    """租户 ID：优先 JWT claim，否则等同 user_id。"""
    state = request.scope.get("state", {}) or {}
    raw = state.get("tenant_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return current_user_id(request, required=required)


def current_role(request: Request) -> str:
    state = request.scope.get("state", {}) or {}
    return str(state.get("role") or "user")


def require_user_tenant(request: Request) -> Tuple[int, int]:
    """返回 (user_id, tenant_id)，缺一不可。"""
    uid = current_user_id(request, required=True)
    tid = current_tenant_id(request, required=True)
    assert uid is not None and tid is not None
    return uid, tid
