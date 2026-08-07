# backend/tests/integration/test_auth.py
"""阶段2 auth 端点集成测试(Task 2.2)。

测试对象: /api/auth/register | login | refresh | logout | me

策略
----
项目 DB 是 Postgres-heavy(三库),用 sqlite 临时库重建 schema 风险较大(0001 baseline
是 create_all 三库)。这里采用 spec 推荐的「实用路径」:用 FastAPI TestClient 打真实
core 库,每个用例用一个带随机后缀的唯一用户名,finally 里清理掉本次产生的 user +
refresh_tokens,保证幂等可重跑。

覆盖:
  - register 返回 access+refresh+user,tier 默认 free
  - login 正确密码 200 / 错误密码 401
  - refresh 轮换:旧 jti revoked,新 token 可继续用
  - logout 撤销 jti;登出后再 refresh 该 jti 返回 401
  - me:无 token 返回 401(Task 2.3 后由 middleware 守卫,见 test_auth_middleware.py)
  - 重复 register 同名 400
"""
from __future__ import annotations

import secrets
import time

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module  # 触发 app 装配(含 get_db)
from backend.database.connection import SessionLocal
from backend.database.models import RefreshToken, User


@pytest.fixture(scope="module")
def client():
    # backend.main 在 import 时已完成 app 装配 + include_router(auth_router)。
    return TestClient(main_module.app)


def _unique(prefix: str = "authtest") -> str:
    """带时间戳+随机的唯一用户名/邮箱,避免多轮重跑撞名。"""
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{secrets.token_hex(3)}"


def _cleanup(username: str, email: str) -> None:
    """删除测试产生的 user 及其 refresh_tokens 行,保证幂等。"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()
            db.delete(user)
            db.commit()
        # 兜底:按 email 也清一次(防止 user 行没建但 email 唯一索引残留)
        by_email = db.query(User).filter(User.email == email).first()
        if by_email:
            db.query(RefreshToken).filter(RefreshToken.user_id == by_email.id).delete()
            db.delete(by_email)
            db.commit()
    finally:
        db.close()


def test_register_returns_tokens(client):
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        resp = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": "S3cret-pw"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["user"]["username"] == username
        assert body["user"]["email"] == email
        assert body["user"]["tier"] == "free"  # server_default 兜底

        # register 同时应落一条 refresh_tokens 行(revoked=false)
        db = SessionLocal()
        try:
            from backend.core.security import decode_token

            jti = decode_token(body["refresh_token"])["jti"]
            rec = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
            assert rec is not None and rec.revoked == "false"
        finally:
            db.close()
    finally:
        _cleanup(username, email)


def test_register_duplicate_username_400(client):
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        r1 = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": "S3cret-pw"},
        )
        assert r1.status_code == 200, r1.text
        # 同名再注册(换 email 避开 email 唯一索引)
        r2 = client.post(
            "/api/auth/register",
            json={"username": username, "email": f"{username}_2@example.com", "password": "S3cret-pw"},
        )
        assert r2.status_code == 400
        assert "taken" in r2.json()["detail"].lower()
    finally:
        _cleanup(username, email)
        _cleanup(username, f"{username}_2@example.com")


def test_login_with_correct_and_wrong_password(client):
    username = _unique()
    email = f"{username}@example.com"
    password = "correct-S3cret"
    _cleanup(username, email)
    try:
        reg = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        assert reg.status_code == 200, reg.text

        ok = client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["user"]["username"] == username

        bad = client.post(
            "/api/auth/login", json={"username": username, "password": "wrong-password"}
        )
        assert bad.status_code == 401
        assert "invalid" in bad.json()["detail"].lower()

        # email 登录分流
        ok_email = client.post(
            "/api/auth/login", json={"username": email, "password": password}
        )
        assert ok_email.status_code == 200, ok_email.text
        assert ok_email.json()["user"]["id"] == ok.json()["user"]["id"]
    finally:
        _cleanup(username, email)


def test_refresh_rotates_and_revokes_old_jti(client):
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        reg = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": "S3cret-pw"},
        )
        assert reg.status_code == 200, reg.text
        old_refresh = reg.json()["refresh_token"]

        # refresh 一次 → 拿到新对,旧 jti 应被撤销
        r1 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        assert r1.status_code == 200, r1.text
        new_body = r1.json()
        assert new_body["refresh_token"] != old_refresh

        # 旧 refresh token 再用 → 401(revoked)
        r2 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
        assert r2.status_code == 401

        # 新 refresh token 能继续用
        r3 = client.post("/api/auth/refresh", json={"refresh_token": new_body["refresh_token"]})
        assert r3.status_code == 200, r3.text
    finally:
        _cleanup(username, email)


def test_logout_revokes_and_is_idempotent(client):
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        reg = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": "S3cret-pw"},
        )
        assert reg.status_code == 200, reg.text
        refresh_tok = reg.json()["refresh_token"]

        lo = client.post("/api/auth/logout", json={"refresh_token": refresh_tok})
        assert lo.status_code == 200
        assert lo.json()["detail"] == "logged out"

        # 登出后再 refresh 该 token → 401
        after = client.post("/api/auth/refresh", json={"refresh_token": refresh_tok})
        assert after.status_code == 401

        # logout 幂等:对已撤销/无效 token 再调仍 200
        lo2 = client.post("/api/auth/logout", json={"refresh_token": "garbage.token.here"})
        assert lo2.status_code == 200
        assert lo2.json()["detail"] == "logged out"
    finally:
        _cleanup(username, email)


def test_me_returns_401_without_token(client):
    # Task 2.3 后 /me 由 JWTAuthMiddleware 守卫:GET 无 token 不注入身份,
    # 端点读到 state.user_id 缺失 → 401。带 token 的正路径由
    # test_auth_middleware.py 覆盖。
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
