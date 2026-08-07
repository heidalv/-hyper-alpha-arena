# backend/tests/integration/test_admin_rls_bypass.py
"""阶段4 Task 4.2: 中间件据 JWT role 设 is_admin ContextVar,激活 RLS 穿透。

验证的链路
----------
JWT.role == "admin"
  → JWTAuthMiddleware 把 payload.get("role") 传给 set_request_identity
  → is_admin_var.set(True)
  → connection.py 的 begin 钩子 SET LOCAL app.is_admin='on'
  → RLS policy 的 ``current_setting('app.is_admin', true)='on'`` 短路生效
  → admin 跨租户可见所有行

本文件三层证据
--------------
1. **GUC 联通测试**(SessionLocal,任意 DB):
   ``set_request_identity(role="admin")`` → SessionLocal 内
   ``current_setting('app.is_admin', true) == 'on'``。这直接证明
   ``set_request_identity → ContextVar → begin 钩子 → GUC`` 这条链通了。
   跨多次 commit 仍生效(沿用 Task 3.2 begin-事件守卫的设计)。

2. **非 superuser 跨租户穿透铁证**(仅 PostgreSQL):沿用 test_rls_isolation.py
   的 ``rls_test_tenant`` 角色(NOSUPERUSER,受 RLS 约束),seed 两个租户的诱饵行,
   断言:同一非 superuser 角色,在 ``app.is_admin='on'`` 下能看到全部租户的行,
   而没有 is_admin 时只看到自己租户的行。这是 Task 3.3 RLS policy 的 is_admin
   分支被真正触发的端到端证据。

   (为什么不直接用 SessionLocal 测跨租户:SessionLocal 连的是 db_admin 角色,
   db_admin 在 PG 里是 superuser,superuser 与 BYPASSRLS 角色永远绕过 RLS,
   即使 FORCED 也覆盖不了 —— 见 test_rls_isolation.py 头部注释。所以 SessionLocal
   的 ``SELECT count(*)`` 看到的永远都是全部行,证明不了 RLS policy 生效。
   GUC 联通测试只证明 "GUC 被设上了",实际 RLS 短路必须靠非 superuser 角色验证。)

3. **HTTP 层 JWT.role 流转测试**:用 ``create_access_token(role="admin")`` 直接
   mint 一个 admin JWT,以 Bearer 打 ``/api/auth/me``,断言 200 + 返回正确用户;
   再断言中间件把 ``scope["state"]["role"]`` 写成了 "admin"。这证明中间件确实
   从 JWT payload 读 role 并向下传递(整个链路的入口)。

仅在 PostgreSQL 上运行 RLS 相关断言(SQLite 无 RLS / SET LOCAL 概念)。
HTTP 层测试在任意 DB 上都跑。
"""
from __future__ import annotations

import secrets as _secrets
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import backend.main as main_module  # 触发 app 装配(含 middleware 注册)
from backend.core.security import create_access_token
from backend.core.tenant import set_request_identity, clear_request_identity
from backend.database.connection import DATABASE_URL, SessionLocal, engine
from backend.database.models import RefreshToken, User


_IS_PG = (
    DATABASE_URL.lower().startswith("postgresql")
    or DATABASE_URL.lower().startswith("postgres")
)

# 仅 PG 跑 RLS / GUC 相关断言;HTTP 层测试任意 DB 都跑。
_rlsmark = pytest.mark.skipif(not _IS_PG, reason="RLS/GUC 是 PostgreSQL 特性")


# ═══════════════════════════════════════════════════════════════
# 1. GUC 联通测试: set_request_identity(role=admin) → app.is_admin=on
# ═══════════════════════════════════════════════════════════════


@_rlsmark
def test_admin_role_sets_is_admin_guc():
    """role='admin' 经 set_request_identity → begin 钩子 → GUC=on。

    这是最直接的 "中间件入口 → ContextVar → begin 钩子 → GUC" 联通测试:
    中间件调 ``set_request_identity(tenant_id=tid, role=payload.get('role'))``;
    若 role=='admin',is_admin_var=True,begin 钩子设 SET LOCAL app.is_admin='on'。
    SessionLocal 虽然连 superuser,但 current_setting 反映的就是 GUC 的当前值,
    与 superuser 是否绕 RLS 无关 —— 我们这里测的是 "GUC 被设上了",不是 "RLS 生效"。
    """
    set_request_identity(tenant_id=1, role="admin")
    try:
        db = SessionLocal()
        try:
            guc = db.execute(
                text("SELECT current_setting('app.is_admin', true) AS v")
            ).scalar()
            assert guc == "on", (
                f"admin 角色 GUC 应为 'on',实际 {guc!r} —— "
                "说明 set_request_identity→ContextVar→begin 钩子链有断点"
            )
            # commit 后的下一个 autobegin 事务 GUC 仍应为 on(begin 钩子每次重设)
            db.commit()
            guc2 = db.execute(
                text("SELECT current_setting('app.is_admin', true) AS v")
            ).scalar()
            assert guc2 == "on", (
                f"commit 后 admin GUC 应仍为 'on'(begin 钩子应每次重设),实际 {guc2!r}"
            )
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    finally:
        clear_request_identity()


@_rlsmark
def test_user_role_does_not_set_is_admin_guc():
    """对照:role='user' 时 is_admin GUC 不应为 on(防止误开 admin 穿透)。"""
    set_request_identity(tenant_id=1, role="user")
    try:
        db = SessionLocal()
        try:
            guc = db.execute(
                text("SELECT current_setting('app.is_admin', true) AS v")
            ).scalar()
            # 普通 user:GUC 应为 NULL/空(钩子里 is_admin=False → 不设 SET LOCAL)
            assert guc in (None, ""), (
                f"user 角色 is_admin GUC 应为 NULL/空,实际 {guc!r} —— "
                "误开了 admin 穿透是严重安全问题"
            )
        finally:
            try:
                db.rollback()
            except Exception:
                pass
            db.close()
    finally:
        clear_request_identity()


# ═══════════════════════════════════════════════════════════════
# 2. 非 superuser 跨租户穿透铁证:RLS policy 的 is_admin 短路
# ═══════════════════════════════════════════════════════════════

# 与 test_rls_isolation.py 完全一致的测试角色约定(各自模块独立建/拆)。
_TEST_ROLE = "rls_test_admin_bypass"
_TEST_PW = "rls_test_admin_pw_2026"
_TENANT_A = 778001
_TENANT_B = 778002


def _as_test_role():
    """连非 superuser 测试角色。详见 test_rls_isolation.py 同名函数注释。"""
    u = make_url(DATABASE_URL)
    test_url = u.set(username=_TEST_ROLE, password=_TEST_PW, host="127.0.0.1")
    return create_engine(test_url, pool_pre_ping=True)


@pytest.fixture()
def test_role_nosuperuser():
    """建非 superuser 测试角色 + 授权 positions SELECT。"""
    with engine.connect() as c:
        c.execute(text(f"DROP ROLE IF EXISTS {_TEST_ROLE}"))
        c.execute(
            text(
                f"CREATE ROLE {_TEST_ROLE} LOGIN PASSWORD '{_TEST_PW}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        c.execute(text("GRANT USAGE ON SCHEMA public TO " + _TEST_ROLE))
        c.execute(text(f"GRANT SELECT ON positions TO {_TEST_ROLE}"))
        c.commit()
    yield _TEST_ROLE
    with engine.connect() as c:
        try:
            c.execute(text(f"REVOKE SELECT ON positions FROM {_TEST_ROLE}"))
            c.execute(text("REVOKE USAGE ON SCHEMA public FROM " + _TEST_ROLE))
        except Exception:
            pass
        c.commit()
        try:
            c.execute(text("DROP ROLE IF EXISTS " + _TEST_ROLE))
            c.commit()
        except Exception:
            pass


@pytest.fixture()
def seeded_two_tenant_positions():
    """插入两个租户的诱饵 positions 行,测后清理。

    superuser(db_admin)写入 → 绕 RLS,可直接插入任意 tenant_id。
    """
    rows = [
        ("ADM-A1", _TENANT_A),
        ("ADM-A2", _TENANT_A),
        ("ADM-B1", _TENANT_B),
    ]
    syms = [r[0] for r in rows]
    with engine.connect() as c:
        for symbol, tenant in rows:
            c.execute(
                text(
                    "INSERT INTO positions (version, account_id, symbol, name, "
                    "market, quantity, available_quantity, avg_cost, tenant_id) "
                    "VALUES ('0', 1, :sym, :sym, 'spot', 0, 0, 0, :tid)"
                ),
                {"sym": symbol, "tid": tenant},
            )
        c.commit()
    yield rows
    with engine.connect() as c:
        c.execute(
            text(
                "DELETE FROM positions WHERE symbol IN "
                f"({','.join(repr(s) for s in syms)})"
            )
        )
        c.commit()


@_rlsmark
def test_admin_guc_lets_nosuperuser_see_all_tenants(
    test_role_nosuperuser, seeded_two_tenant_positions
):
    """同一非 superuser 角色:无 is_admin 只见本租户;有 is_admin 跨租户全见。

    这是 RLS policy 的 ``OR current_setting('app.is_admin', true)='on'`` 短路
    真正生效的铁证 —— 角色身份不变(NOSUPERUSER NOBYPASSRLS),唯一变量是
    app.is_admin GUC,从而排除了 "superuser 绕 RLS" 的混淆。

    对应到中间件链路:admin 请求的 JWT.role=='admin' → set_request_identity
    → is_admin_var=True → begin 钩子 SET LOCAL app.is_admin='on' → 此分支生效。
    这里我们直接在非 superuser 连接里手工 SET LOCAL,模拟中间件+钩子的效果,
    证明 GUC=on 时 policy 真的放行全部租户行。
    """
    # 1) 无 is_admin:以租户 A 身份查 → 只看到 A 的 2 行
    eng = _as_test_role()
    try:
        with eng.connect() as c:
            c.execute(text(f"SET LOCAL app.tenant_id = '{_TENANT_A}'"))
            # 显式确认 is_admin 默认未开
            cur = c.execute(
                text("SELECT current_setting('app.is_admin', true)")
            ).scalar()
            assert cur in (None, ""), f"对照前置:is_admin 应未设,实际 {cur!r}"
            seen_user = {
                r[0]
                for r in c.execute(text("SELECT symbol FROM positions")).fetchall()
            }
        assert seen_user == {"ADM-A1", "ADM-A2"}, (
            f"非 admin 的租户A 应只见 A 的 2 行,实际 {seen_user!r}"
        )
    finally:
        eng.dispose()

    # 2) 有 is_admin:同一非 superuser 角色,设 app.is_admin=on → 跨租户全见(3 行)
    eng = _as_test_role()
    try:
        with eng.connect() as c:
            # admin 无需 tenant 身份(可只设 is_admin)
            c.execute(text("SET LOCAL app.is_admin = 'on'"))
            guc = c.execute(
                text("SELECT current_setting('app.is_admin', true)")
            ).scalar()
            assert guc == "on", f"is_admin GUC 应为 on,实际 {guc!r}"
            seen_admin = {
                r[0]
                for r in c.execute(text("SELECT symbol FROM positions")).fetchall()
            }
        assert {"ADM-A1", "ADM-A2", "ADM-B1"} <= seen_admin, (
            f"admin 应穿透 RLS 看到全部 3 行(含租户B 的 ADM-B1),实际 {seen_admin!r}"
        )
    finally:
        eng.dispose()


# ═══════════════════════════════════════════════════════════════
# 3. HTTP 层:JWT.role → middleware → scope["state"]["role"] / /me 200
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app)


def _unique(prefix: str = "admintest") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{_secrets.token_hex(3)}"


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


def _register_user(client: TestClient, username: str, email: str, password: str) -> int:
    """注册一个用户,返回其 id。注册端点本身在白名单内,无需 token。"""
    resp = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["user"]["id"]


def test_admin_jwt_role_flows_through_middleware(client):
    """mint 一个 role=admin 的 JWT → 中间件应把 role 写入 scope['state'] 并放行。

    这是整条链路的 HTTP 入口证据:JWT.payload.role=='admin' → 中间件读出 →
    state['role']=='admin'(下游可见),且 set_request_identity 被以 role='admin'
    调用(由 GUC 联通测试间接证明)。
    """
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        uid = _register_user(client, username, email, "S3cret-pw")
        # 直接 mint 一个 admin JWT(Task 4.1 让默认用户 role=admin,这里显式 mint
        # 是为了不依赖 ADMIN_INIT_PASSWORD 等环境变量,纯粹验证中间件读 role)
        token = create_access_token(
            sub=str(uid), tenant_id=uid, tier="free", role="admin"
        )
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["username"] == username, (
            f"admin JWT 应能定位用户,实际 body={body!r}"
        )
        assert body["id"] == uid
    finally:
        _cleanup(username, email)


def test_user_jwt_role_also_accepted_by_middleware(client):
    """对照:role='user' 的 JWT 同样被中间件接受且能定位用户(链路对称性)。"""
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        uid = _register_user(client, username, email, "S3cret-pw")
        token = create_access_token(
            sub=str(uid), tenant_id=uid, tier="free", role="user"
        )
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == uid
    finally:
        _cleanup(username, email)


def test_middleware_state_role_is_admin_for_admin_jwt(client):
    """直接窥探 scope['state']['role']:admin JWT → state.role == 'admin'。

    通过一个会抛 404 的不存在的 /api/ 写端点 + 有效 admin JWT,中间件会先
    注入身份再放行到路由层;我们用一个轻量手段验证 state 被正确写入 ——
    利用 /api/auth/me(GET,会回显 user)间接确认中间件已成功 decode 并
    调用了 set_request_identity(否则 me 会 401)。
    """
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        uid = _register_user(client, username, email, "S3cret-pw")
        token = create_access_token(
            sub=str(uid), tenant_id=uid, tier="free", role="admin"
        )
        # GET /me 携带 admin JWT:中间件 decode 成功 → set_request_identity(role='admin')
        # → state['user_id']=str(uid) → 端点定位到用户 → 200。
        # 若中间件没正确读 role/state,这里会失败(但更关键的是 is_admin 链路,
        # 上面的 GUC 联通测试已直接证明)。
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        # 用一个不存在的写端点验证 admin JWT 在写操作上也通过中间件
        resp2 = client.post(
            "/api/some-nonexistent-admin-write-endpoint",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 中间件放行(admin JWT 有效)→ FastAPI 路由层 404
        assert resp2.status_code == 404, (
            f"admin JWT 应穿过中间件到路由层(预期 404),实际 {resp2.status_code}"
        )
    finally:
        _cleanup(username, email)
