# backend/tests/integration/test_admin_routes.py
"""阶段4 Task 4.3: /api/admin/* 管理路由 + require_admin + 审计表。

验证矩阵
--------
1. **require_admin 守卫**(role != admin 一律 403):
   - 普通 user JWT 打 /api/admin/users → 403。
   - 无 token 打 /api/admin/users → 401(中间件:GET 虽开放但无身份;不过 require_admin
     依赖前中间件对写操作会拦,这里重点验 admin 路由组的 403 语义)。

2. **admin CRUD**:
   - admin JWT 打 /api/admin/users → 200 + 列表(含被测用户)。
   - admin PATCH tier → 200 + 审计日志落 admin_audit_logs。
   - admin PATCH status → 200 + 审计日志。

3. **防自停**:admin PATCH 自己 status → 400(不能把自己锁死)。

4. **stats / audit-logs**:admin 可读,返回结构正确。

mint JWT 的方式沿用 test_admin_rls_bypass.py:用 create_access_token 直接签发
role=admin / role=user 的 token,不依赖 ADMIN_INIT_PASSWORD 等环境变量,
纯粹验证 require_admin 依赖的判定逻辑。注册端点(/api/auth/register)在白名单内,
可匿名创建"靶子"用户供 admin 操作。
"""
from __future__ import annotations

import secrets as _secrets
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import backend.main as main_module  # 触发 app 装配(含 admin router 注册 + 中间件)
from backend.core.security import create_access_token
from backend.database.connection import SessionLocal
from backend.database.models import AdminAuditLog, RefreshToken, User


@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app)


def _unique(prefix: str = "admintest") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{_secrets.token_hex(3)}"


def _cleanup_user(user_id: int | None, username: str | None, email: str | None) -> None:
    """彻底清掉一个测试用户及其审计记录 / refresh token。"""
    db = SessionLocal()
    try:
        targets = []
        if user_id is not None:
            u = db.query(User).filter(User.id == user_id).first()
            if u:
                targets.append(u)
        if username is not None:
            for u in db.query(User).filter(User.username == username).all():
                if u not in targets:
                    targets.append(u)
        if email is not None:
            for u in db.query(User).filter(User.email == email).all():
                if u not in targets:
                    targets.append(u)
        for u in targets:
            db.query(RefreshToken).filter(RefreshToken.user_id == u.id).delete()
            db.query(AdminAuditLog).filter(
                (AdminAuditLog.admin_user_id == u.id)
                | (AdminAuditLog.target_user_id == u.id)
            ).delete(synchronize_session=False)
            db.delete(u)
        db.commit()
    finally:
        db.close()


def _register_user(client: TestClient, username: str, email: str, password: str) -> int:
    """注册一个用户,返回其 id。注册端点在白名单内,无需 token。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["user"]["id"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════
# 1. require_admin 守卫:非 admin → 403
# ═══════════════════════════════════════════════════════════════


def test_non_admin_jwt_gets_403(client):
    """role='user' 的 JWT 打 /api/admin/users → 403(require_admin 拒绝)。"""
    username = _unique("target")
    email = f"{username}@example.com"
    _cleanup_user(None, username, email)
    try:
        uid = _register_user(client, username, email, "S3cret-pw")
        # 显式 mint 一个 role=user 的 token(注册返回的 token 不带 role claim,
        # 这里用 create_access_token 精确控制 role='user')
        token = create_access_token(
            sub=str(uid), tenant_id=uid, tier="free", role="user"
        )
        resp = client.get("/api/admin/users", headers=_bearer(token))
        assert resp.status_code == 403, (
            f"普通 user 应被 require_admin 拒(403),实际 {resp.status_code}: {resp.text}"
        )
    finally:
        _cleanup_user(uid, username, email)


def test_missing_token_gets_rejected(client):
    """无 token 打 admin GET → 401 或 403,总之不是 200。

    /api/admin/users 是 GET,中间件默认放行普通 GET;但 require_admin 依赖会在
    role 为 None 时 403。无论中间件先 401 还是依赖先 403,都不应是 200。
    """
    resp = client.get("/api/admin/users")
    assert resp.status_code in (401, 403), (
        f"无 token 访问 admin 路由不应放行,实际 {resp.status_code}"
    )


# ═══════════════════════════════════════════════════════════════
# 2. admin CRUD + 审计
# ═══════════════════════════════════════════════════════════════


def test_admin_lists_users(client):
    """admin JWT 打 /api/admin/users → 200,且返回的列表包含刚注册的靶子用户。"""
    adminname = _unique("adminL")
    admin_email = f"{adminname}@example.com"
    target_name = _unique("targetL")
    target_email = f"{target_name}@example.com"
    _cleanup_user(None, adminname, admin_email)
    _cleanup_user(None, target_name, target_email)
    try:
        admin_id = _register_user(client, adminname, admin_email, "S3cret-pw")
        target_id = _register_user(client, target_name, target_email, "S3cret-pw")
        token = create_access_token(
            sub=str(admin_id), tenant_id=admin_id, tier="free", role="admin"
        )
        resp = client.get("/api/admin/users", headers=_bearer(token))
        assert resp.status_code == 200, resp.text
        users = resp.json()
        ids = {u["id"] for u in users}
        assert target_id in ids, (
            f"admin 应能跨租户看到靶子用户 {target_id},返回 ids={ids}"
        )
        # 顺带验证返回结构含必要字段
        target_row = next(u for u in users if u["id"] == target_id)
        assert "tier" in target_row and "role" in target_row and "is_active" in target_row
    finally:
        _cleanup_user(admin_id, adminname, admin_email)
        _cleanup_user(target_id, target_name, target_email)


def test_admin_set_tier_creates_audit_log(client):
    """admin PATCH tier → 200,且 admin_audit_logs 落一条 set_tier 记录。"""
    adminname = _unique("adminT")
    admin_email = f"{adminname}@example.com"
    target_name = _unique("targetT")
    target_email = f"{target_name}@example.com"
    _cleanup_user(None, adminname, admin_email)
    _cleanup_user(None, target_name, target_email)
    try:
        admin_id = _register_user(client, adminname, admin_email, "S3cret-pw")
        target_id = _register_user(client, target_name, target_email, "S3cret-pw")
        token = create_access_token(
            sub=str(admin_id), tenant_id=admin_id, tier="free", role="admin"
        )
        # 先清掉历史审计行(同 target 可能因重跑残留)
        db = SessionLocal()
        try:
            db.query(AdminAuditLog).filter(
                AdminAuditLog.target_user_id == target_id
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

        resp = client.patch(
            f"/api/admin/users/{target_id}/tier",
            json={"tier": "vip"},
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == target_id and body["tier"] == "vip", (
            f"tier 更新应返回新值,实际 {body!r}"
        )

        # 验证审计日志
        db = SessionLocal()
        try:
            logs = (
                db.query(AdminAuditLog)
                .filter(
                    AdminAuditLog.admin_user_id == admin_id,
                    AdminAuditLog.target_user_id == target_id,
                    AdminAuditLog.action == "set_tier",
                )
                .all()
            )
            assert len(logs) >= 1, "set_tier 应至少落一条审计日志"
            last = logs[-1]
            assert last.detail is not None, "detail 不应为空"
            # detail 存的是 {old, new}
            detail = last.detail if isinstance(last.detail, dict) else None
            assert detail and detail.get("new") == "vip", (
                f"审计 detail.new 应为 'vip',实际 {last.detail!r}"
            )
        finally:
            db.close()
    finally:
        _cleanup_user(admin_id, adminname, admin_email)
        _cleanup_user(target_id, target_name, target_email)


def test_admin_cannot_disable_self(client):
    """admin PATCH 自己 status → 400(防自停,不能把自己锁死)。"""
    adminname = _unique("adminSelf")
    admin_email = f"{adminname}@example.com"
    _cleanup_user(None, adminname, admin_email)
    try:
        admin_id = _register_user(client, adminname, admin_email, "S3cret-pw")
        token = create_access_token(
            sub=str(admin_id), tenant_id=admin_id, tier="free", role="admin"
        )
        resp = client.patch(
            f"/api/admin/users/{admin_id}/status",
            json={"is_active": "false"},
            headers=_bearer(token),
        )
        assert resp.status_code == 400, (
            f"admin 不应能停用自己(预期 400),实际 {resp.status_code}: {resp.text}"
        )
    finally:
        _cleanup_user(admin_id, adminname, admin_email)


def test_admin_set_status_on_other_creates_audit(client):
    """admin PATCH 别人 status → 200 + 审计日志(对照:非自停路径正常工作)。"""
    adminname = _unique("adminS")
    admin_email = f"{adminname}@example.com"
    target_name = _unique("targetS")
    target_email = f"{target_name}@example.com"
    _cleanup_user(None, adminname, admin_email)
    _cleanup_user(None, target_name, target_email)
    try:
        admin_id = _register_user(client, adminname, admin_email, "S3cret-pw")
        target_id = _register_user(client, target_name, target_email, "S3cret-pw")
        token = create_access_token(
            sub=str(admin_id), tenant_id=admin_id, tier="free", role="admin"
        )
        db = SessionLocal()
        try:
            db.query(AdminAuditLog).filter(
                AdminAuditLog.target_user_id == target_id
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

        resp = client.patch(
            f"/api/admin/users/{target_id}/status",
            json={"is_active": "false"},
            headers=_bearer(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] == "false"

        db = SessionLocal()
        try:
            logs = (
                db.query(AdminAuditLog)
                .filter(
                    AdminAuditLog.admin_user_id == admin_id,
                    AdminAuditLog.target_user_id == target_id,
                    AdminAuditLog.action == "set_status",
                )
                .all()
            )
            assert len(logs) >= 1, "set_status 应落审计日志"
        finally:
            db.close()
    finally:
        _cleanup_user(admin_id, adminname, admin_email)
        _cleanup_user(target_id, target_name, target_email)


# ═══════════════════════════════════════════════════════════════
# 3. 读端点:stats / audit-logs(admin 可读)
# ═══════════════════════════════════════════════════════════════


def test_admin_stats_and_audit_logs(client):
    """admin GET /stats 与 /audit-logs → 200,返回结构正确。"""
    adminname = _unique("adminR")
    admin_email = f"{adminname}@example.com"
    target_name = _unique("targetR")
    target_email = f"{target_name}@example.com"
    _cleanup_user(None, adminname, admin_email)
    _cleanup_user(None, target_name, target_email)
    try:
        admin_id = _register_user(client, adminname, admin_email, "S3cret-pw")
        target_id = _register_user(client, target_name, target_email, "S3cret-pw")
        token = create_access_token(
            sub=str(admin_id), tenant_id=admin_id, tier="free", role="admin"
        )
        # 先做一次写操作产生审计,再读 audit-logs
        patch = client.patch(
            f"/api/admin/users/{target_id}/tier",
            json={"tier": "pro"},
            headers=_bearer(token),
        )
        assert patch.status_code == 200, patch.text

        # stats
        s = client.get("/api/admin/stats", headers=_bearer(token))
        assert s.status_code == 200, s.text
        sbody = s.json()
        assert "total_users" in sbody and "by_tier" in sbody
        assert sbody["total_users"] >= 2, (
            f"至少应有 admin + target 两个用户,实际 {sbody['total_users']}"
        )

        # audit-logs
        a = client.get("/api/admin/audit-logs", headers=_bearer(token))
        assert a.status_code == 200, a.text
        alogs = a.json()
        assert isinstance(alogs, list)
        # 倒序最新一条应是我们刚写的 set_tier
        assert alogs, "audit-logs 不应为空(刚写过 tier)"
        latest = alogs[0]
        assert latest["action"] == "set_tier", (
            f"最新审计应为 set_tier,实际 {latest['action']!r}"
        )
        assert latest["admin_user_id"] == admin_id
        assert latest["target_user_id"] == target_id
    finally:
        _cleanup_user(admin_id, adminname, admin_email)
        _cleanup_user(target_id, target_name, target_email)
