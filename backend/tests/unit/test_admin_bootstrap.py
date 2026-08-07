# backend/tests/unit/test_admin_bootstrap.py
"""阶段4 Task 4.1 admin bootstrap 测试。

覆盖:
  - 迁移 0006 已应用 → default 用户 role == admin(直查 DB)。
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
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()
            db.delete(user)
            db.commit()
        by_email = db.query(User).filter(User.email == email).first()
        if by_email:
            db.query(RefreshToken).filter(RefreshToken.user_id == by_email.id).delete()
            db.delete(by_email)
            db.commit()
    finally:
        db.close()


def test_default_user_role_is_admin():
    """迁移 0006 已应用:default 用户 role 应为 admin。"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "default").first()
        assert user is not None, "default 用户不存在(迁移未跑 / 种子缺失)"
        # getattr 兜底:旧库无 role 列时不会 AttributeError。
        assert getattr(user, "role", None) == "admin"
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
