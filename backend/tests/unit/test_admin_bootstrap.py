# backend/tests/unit/test_admin_bootstrap.py
"""阶段4 Task 4.1 admin bootstrap 测试。

覆盖:
  - 迁移 0006 已应用 → 库中至少一个 admin(直查 DB;不钉死具体用户名)。
  - 新注册用户 role == user(server_default 兜底)。
  - create_access_token(role=admin) 往返解出 role=admin。

DB 策略同 test_auth.py / test_user_repo_password.py:打真实 core 库,用带
随机后缀的唯一用户名,finally 清理。
"""
from __future__ import annotations

import secrets
import time

from backend.core.security import create_access_token, decode_token
from backend.database.connection import SessionLocal
from backend.database.models import RefreshToken, User
from backend.repositories.user_repo import create_user


def _unique(prefix: str = "admintest") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{secrets.token_hex(3)}"


def _cleanup(username: str, email: str) -> None:
    """[2026-09-02] 改走共享 helper（RLS 下删子表需 admin 穿透）。
    详见 backend/tests/_user_cleanup.py。"""
    from backend.tests._user_cleanup import cleanup_user
    cleanup_user(username=username, email=email)


def test_admin_bootstrap_guarantees_at_least_one_admin():
    """迁移 0006 的不变量:库中至少有一个 role=admin 的用户。

    [2026-09-02] 原用例钉死"username=='default' 必须是 admin"。迁移文档写的是
    "default 用户（或 id 最小的用户）设为 admin —— 保证至少有一个 admin"，目的
    是不变量而非某个用户名。本地部署里 admin 是真实属主 heida(326，即
    AUTH_LOCAL_TENANT)，default(327) 是后建的普通用户，旧断言在这里长期红，
    但系统状态完全符合迁移意图。改为验证不变量本身。
    """
    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == "admin").all()
        assert admins, "库中没有任何 admin 用户（迁移 0006 bootstrap 未生效）"
    finally:
        db.close()


def test_newly_registered_user_role_is_user():
    """create_user 新建的用户 role 应为 'user'(server_default 兜底)。"""
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        db = SessionLocal()
        try:
            user = create_user(db, username, email, "S3cret-pw")
            db.refresh(user)
            # role 列有 server_default='user';getattr 兜底。
            assert getattr(user, "role", "user") == "user"
        finally:
            db.close()
    finally:
        _cleanup(username, email)


def test_access_token_admin_role_roundtrip():
    """create_access_token(role=admin) 解出 role=admin(供 4.2 中间件判断)。"""
    t = create_access_token(sub="1", tenant_id=1, tier="free", role="admin")
    payload = decode_token(t)
    assert payload["role"] == "admin"
    assert payload["sub"] == "1"
    assert payload["type"] == "access"
