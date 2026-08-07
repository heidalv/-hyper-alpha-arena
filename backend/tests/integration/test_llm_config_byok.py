# backend/tests/integration/test_llm_config_byok.py
"""阶段3 Task 3.4: LLM 配置 BYOK 隔离路由测试(应用层 + DB 层双保险)。

测什么
------
1. POST /api/llm-configs 必须认证,且把新配置 stamp 上调用者的 tenant_id
   (BYOK §6.4 —— 不允许全局/NULL tenant 的 LLM 配置)。
2. GET /{id}/api-key 改返回部分掩码 key(``sk-****abcd``),不再泄露完整明文。
3. 跨租户隔离由 RLS 兜底:租户 A 创建的配置,租户 B 在 DB 层看不到
   (用非 superuser 测试角色直接验证 RLS,绕开"db_admin 是 superuser 绕过 RLS"
   的 HTTP 层限制 —— 见下方说明)。

superuser 测试限制(关键背景)
-----------------------------
生产/开发库的 Alembic 连接角色 ``db_admin`` 在 PostgreSQL 里是 superuser,
superuser 永远绕过 RLS(``BYPASSRLS`` 也一样,``FORCE`` 都覆盖不了)。
因此 HTTP 路由用 TestClient 打库时,所有查询都以 db_admin 身份执行 ——
无论中间件怎么设 ``app.tenant_id`` GUC,RLS 都不参与查询规划,
跨租户读会"看起来"通过(假安全)。

所以本测试分两层:
  - **应用层**(TestClient + 真 JWT):证明路由会 stamp tenant_id、
    会 mask key、会拒绝未认证写入。这些不依赖 RLS,superuser 也成立。
  - **DB 层**(非 superuser 角色 ``rls_test_byok``):证明一旦连库角色不是
    superuser,RLS 真的会过滤掉跨租户的 llm_configurations 行。这与
    test_rls_isolation.py 用同一个非 superuser 角色思路,只是表换成
    llm_configurations。

仅在 PostgreSQL 上运行 RLS 部分;应用层部分两种库都能跑。
"""
from __future__ import annotations

import secrets as _secrets
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import backend.main as main_module  # 触发 app 装配(含 middleware 注册)
from backend.database.connection import DATABASE_URL, SessionLocal, engine
from backend.database.models import LLMConfiguration, RefreshToken, User
from backend.utils.encryption import encrypt_llm_key, decrypt_llm_key


# ── RLS DB 层测试仅在 PostgreSQL 上运行 ──────────────────────────────
_IS_POSTGRES = DATABASE_URL.lower().startswith("postgresql") or DATABASE_URL.lower().startswith("postgres")

# 非 superuser 测试角色(密码/名称写死,测试自身负责建/删)。
_TEST_ROLE = "rls_test_byok"
_TEST_PW = "rls_test_byok_pw_2026"
_TENANT_A = 778001
_TENANT_B = 778002


@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app)


def _unique(prefix: str = "byok") -> str:
    return f"{prefix}_{int(time.time() * 1000) % 10**9}_{_secrets.token_hex(3)}"


def _cleanup_user(username: str, email: str) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()
            # 清掉该用户名下的测试 LLM 配置(避免外键残留)
            db.query(LLMConfiguration).filter(LLMConfiguration.tenant_id == user.id).delete()
            db.delete(user)
            db.commit()
        by_email = db.query(User).filter(User.email == email).first()
        if by_email:
            db.query(RefreshToken).filter(RefreshToken.user_id == by_email.id).delete()
            db.query(LLMConfiguration).filter(LLMConfiguration.tenant_id == by_email.id).delete()
            db.delete(by_email)
            db.commit()
    finally:
        db.close()


def _cleanup_llm(config_id: int) -> None:
    db = SessionLocal()
    try:
        c = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
        if c:
            db.delete(c)
            db.commit()
    finally:
        db.close()


def _register_and_login(client: TestClient, username: str, email: str, password: str) -> tuple[str, int]:
    """注册并登录,返回 (access_token, user_id)。"""
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    token = body["access_token"]
    # 从 /me 取 user_id(即 tenant_id,见 auth_routes.register: tenant_id=user.id)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]
    return token, user_id


# ═══════════════════════════════════════════════════════════════════
# 应用层:POST stamp tenant_id + 鉴权
# ═══════════════════════════════════════════════════════════════════


def test_create_without_token_returns_401(client):
    """未认证 POST 应被拒绝(BYOK 要求配置归属租户)。"""
    resp = client.post(
        "/api/llm-configs",
        json={
            "name": "should-fail",
            "provider": "custom",
            "model": "m",
            "base_url": "http://x/v1",
            "api_key": "sk-test",
        },
    )
    # 中间件对写操作无 token 直接 401
    assert resp.status_code == 401, resp.text


def test_create_stamps_caller_tenant_id(client):
    """认证用户 POST,新配置应带上调用者的 tenant_id(=user.id)。"""
    username = _unique()
    email = f"{username}@example.com"
    _cleanup_user(username, email)
    config_id = None
    try:
        token, user_id = _register_and_login(client, username, email, "S3cret-pw")

        resp = client.post(
            "/api/llm-configs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "BYOK-test-A",
                "provider": "custom",
                "model": "m",
                "base_url": "http://x/v1",
                "api_key": "sk-test-1234567890",
            },
        )
        assert resp.status_code == 200, resp.text
        config_id = resp.json()["id"]

        # 直接查库验证 tenant_id 被正确 stamp(superuser 连接绕 RLS,可读到)
        db = SessionLocal()
        try:
            cfg = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
            assert cfg is not None, "刚创建的配置查不到"
            assert cfg.tenant_id == user_id, (
                f"BYOK stamp 失败:期望 tenant_id={user_id},实际 {cfg.tenant_id!r}"
            )
        finally:
            db.close()
    finally:
        if config_id is not None:
            _cleanup_llm(config_id)
        _cleanup_user(username, email)


# ═══════════════════════════════════════════════════════════════════
# 应用层:/api-key 掩码
# ═══════════════════════════════════════════════════════════════════


def test_api_key_endpoint_returns_masked(client):
    """GET /{id}/api-key 返回部分掩码 key,不泄露完整明文(§6.4.2)。"""
    username = _unique()
    email = f"{username}@example.com"
    _cleanup_user(username, email)
    config_id = None
    full_key = "sk-secret-ABCDEFGHIJ-1234"
    try:
        token, _user_id = _register_and_login(client, username, email, "S3cret-pw")
        resp = client.post(
            "/api/llm-configs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "BYOK-test-mask",
                "provider": "custom",
                "model": "m",
                "base_url": "http://x/v1",
                "api_key": full_key,
            },
        )
        assert resp.status_code == 200, resp.text
        config_id = resp.json()["id"]

        key_resp = client.get(
            f"/api/llm-configs/{config_id}/api-key",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert key_resp.status_code == 200, key_resp.text
        body = key_resp.json()
        returned = body["api_key"]
        # 不得返回完整明文
        assert returned != full_key, f"/api-key 泄露完整 key!返回 {returned!r}"
        # 应是部分掩码形态:前缀 + **** + 末尾
        assert "****" in returned, f"返回值未掩码:{returned!r}"
        assert returned.startswith(full_key[:3]), (
            f"掩码前缀错误:{returned!r}"
        )
        assert returned.endswith(full_key[-4:]), (
            f"掩码后缀错误:{returned!r}"
        )
    finally:
        if config_id is not None:
            _cleanup_llm(config_id)
        _cleanup_user(username, email)


def test_response_list_api_key_masked(client):
    """列表/详情响应里的 api_key_masked 字段也不应是完整 key。"""
    username = _unique()
    email = f"{username}@example.com"
    _cleanup_user(username, email)
    config_id = None
    full_key = "sk-supersecret-key-9999"
    try:
        token, _user_id = _register_and_login(client, username, email, "S3cret-pw")
        resp = client.post(
            "/api/llm-configs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "BYOK-test-listmask",
                "provider": "custom",
                "model": "m",
                "base_url": "http://x/v1",
                "api_key": full_key,
                "is_default": False,
            },
        )
        assert resp.status_code == 200, resp.text
        config_id = resp.json()["id"]

        # 列表端点(带 token 才有 tenant 上下文)
        lst = client.get(
            "/api/llm-configs?active_only=false",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert lst.status_code == 200, lst.text
        masked_fields = {i["api_key_masked"] for i in lst.json()["items"]}
        assert full_key not in masked_fields, "列表返回了完整明文 key"
    finally:
        if config_id is not None:
            _cleanup_llm(config_id)
        _cleanup_user(username, email)


# ═══════════════════════════════════════════════════════════════════
# DB 层:跨租户 RLS 隔离(非 superuser 角色直连验证)
# ═══════════════════════════════════════════════════════════════════

rlsmark = pytest.mark.skipif(
    not _IS_POSTGRES,
    reason="RLS 是 PostgreSQL 特性,SQLite 无此概念,跳过",
)


def _as_test_role():
    """用非 superuser 测试角色连 alpha_arena,返回新 engine。

    同 test_rls_isolation.py 的做法:固定 host=127.0.0.1(IPv4 环回最稳),
    必须把 URL 对象直接传 create_engine(不能 str(url),密码会被渲染成 ***)。
    """
    u = make_url(DATABASE_URL)
    test_url = u.set(username=_TEST_ROLE, password=_TEST_PW, host="127.0.0.1")
    return create_engine(test_url, pool_pre_ping=True)


@pytest.fixture(scope="module")
def rls_role():
    """建立非 superuser 测试角色 + 对 llm_configurations 的 SELECT 授权。"""
    with engine.connect() as c:
        c.execute(text(f"DROP ROLE IF EXISTS {_TEST_ROLE}"))
        c.execute(
            text(
                f"CREATE ROLE {_TEST_ROLE} LOGIN PASSWORD '{_TEST_PW}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
        c.execute(text("GRANT USAGE ON SCHEMA public TO " + _TEST_ROLE))
        c.execute(text("GRANT SELECT ON llm_configurations TO " + _TEST_ROLE))
        c.commit()
    yield _TEST_ROLE
    with engine.connect() as c:
        try:
            c.execute(text("REVOKE SELECT ON llm_configurations FROM " + _TEST_ROLE))
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
def seeded_llm_rows():
    """用 superuser(db_admin,绕 RLS)插入两个租户的 llm_configurations 行。

    tenant_id=NOT NULL(0004),故两行都有明确归属。测后按 name 清理。
    """
    names = ("BYOK-RLS-A", "BYOK-RLS-B")
    with engine.connect() as c:
        for name, tid in [(names[0], _TENANT_A), (names[1], _TENANT_B)]:
            c.execute(
                text(
                    "INSERT INTO llm_configurations "
                    "(name, provider, model, base_url, api_key, is_default, "
                    " is_active, test_status, usage_count, tenant_id) "
                    "VALUES (:n, 'custom', 'm', 'http://x/v1', :k, 'false', "
                    "        'true', 'pending', 0, :tid)"
                ),
                {
                    "n": name,
                    "k": encrypt_llm_key("sk-rls-seed-" + name),
                    "tid": tid,
                },
            )
        c.commit()
    yield names
    with engine.connect() as c:
        c.execute(
            text(
                "DELETE FROM llm_configurations WHERE name IN "
                "('BYOK-RLS-A','BYOK-RLS-B')"
            )
        )
        c.commit()


def _visible_names_as(tenant_id):
    """以非 superuser 角色连库,设 GUC 后查 llm_configurations 可见的 name 集合。"""
    eng = _as_test_role()
    try:
        with eng.connect() as c:
            c.execute(text("SET LOCAL app.tenant_id = '" + str(int(tenant_id)) + "'"))
            rows = c.execute(text("SELECT name FROM llm_configurations")).fetchall()
        return {r[0] for r in rows}
    finally:
        eng.dispose()


@rlsmark
def test_rls_blocks_cross_tenant_llm_config(rls_role, seeded_llm_rows):
    """租户 A 在 DB 层只看到自己的 llm_configurations,看不到 B 的。

    若出现 BYOK-RLS-B,说明 RLS 没过滤 → 跨租户 LLM 配置(含密钥)泄漏。
    这是 RLS 兜底的铁证:即便应用层路由未来漏写 WHERE,DB 也拦得住。
    """
    seen_a = _visible_names_as(_TENANT_A)
    assert "BYOK-RLS-A" in seen_a, f"租户A 看不到自己的配置 {seen_a!r}"
    assert "BYOK-RLS-B" not in seen_a, (
        f"RLS 泄漏!租户 A 看到了 B 的 LLM 配置 {seen_a!r}"
    )

    seen_b = _visible_names_as(_TENANT_B)
    assert "BYOK-RLS-B" in seen_b, f"租户B 看不到自己的配置 {seen_b!r}"
    assert "BYOK-RLS-A" not in seen_b, (
        f"RLS 泄漏!租户 B 看到了 A 的 LLM 配置 {seen_b!r}"
    )
