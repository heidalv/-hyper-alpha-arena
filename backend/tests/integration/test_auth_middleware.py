# backend/tests/integration/test_auth_middleware.py
"""阶段2 Task 2.3: JWTAuthMiddleware 集成测试。

测试对象
--------
backend/middleware/auth.py 的 ``JWTAuthMiddleware``:
  - JWT Bearer token 校验 + 身份注入(scope["state"] → request.state)
  - X-API-Key 运维通道(BACKEND_API_KEY 配置时)
  - 白名单(login/register/refresh/logout/health)无需鉴权
  - 写操作(POST/PUT/DELETE/PATCH,/api/ 下非白名单)要求有效 JWT,否则 401
  - GET 暂开放(无 token 也能读;有有效 token 则注入身份)
  - /api/auth/me 由中间件注入身份,端点据此返回当前用户

策略
----
沿用 test_auth.py 的实用路径: TestClient 打真实 core 库,每个用例用带随机后缀的
唯一用户名,finally 清理 user + refresh_tokens,保证幂等可重跑。

注意: 测试环境 .env 不设 BACKEND_API_KEY,因此 X-API-Key 运维通道在本套测试里
默认关闭;需要覆盖运维通道的用例用 monkeypatch 临时设上 BACKEND_API_KEY 并
手动重建中间件实例。
"""
from __future__ import annotations

import secrets as _secrets
import time

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module  # 触发 app 装配(含 middleware 注册)
from backend.database.connection import SessionLocal
from backend.database.models import RefreshToken, User


@pytest.fixture(scope="module")
def client():
    return TestClient(main_module.app)


def _unique(prefix: str = "mwtest") -> str:
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


def _register_and_login(client: TestClient, username: str, email: str, password: str) -> str:
    """注册并登录,返回 access token。"""
    reg = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert reg.status_code == 200, reg.text
    # register 已经直接签发 token,直接复用 access_token 即可。
    return reg.json()["access_token"]


# ── /api/auth/me 身份注入 ──────────────────────────────────────────


def test_me_with_valid_token_returns_user(client):
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        token = _register_and_login(client, username, email, "S3cret-pw")
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["username"] == username
        assert body["email"] == email
        assert body["tier"] == "free"
        assert isinstance(body["id"], int)
    finally:
        _cleanup(username, email)


def test_me_without_token_returns_401(client):
    # GET /me 无 token:中间件不注入身份,端点读不到 user_id → 401。
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_invalid_token_returns_401(client):
    # GET /me 携带格式错的 token:中间件 decode 失败,GET 上静默跳过不注入身份,
    # 端点读不到 user_id → 401。
    resp = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not.a.valid.token"}
    )
    assert resp.status_code == 401


def test_me_with_tampered_token_returns_401(client):
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        token = _register_and_login(client, username, email, "S3cret-pw")
        # 翻转尾部签名 → 验签失败 → GET 静默跳过 → 端点 401。
        tampered = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
        resp = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {tampered}"}
        )
        assert resp.status_code == 401
    finally:
        _cleanup(username, email)


# ── 白名单:无需 token ─────────────────────────────────────────────


def test_whitelisted_login_works_without_token(client):
    # /api/auth/login 在白名单里,即使方法是 POST 也不要求 JWT。
    # 用一个不存在的用户名测,只要不被中间件 401 拦掉就行(端点自己返回 401
    # invalid credentials,代表请求已穿过中间件到达路由)。
    resp = client.post(
        "/api/auth/login",
        json={"username": _unique("nobody"), "password": "whatever"},
    )
    # 不是中间件的 401 "Not authenticated",而是端点的 "invalid credentials"。
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"


def test_whitelisted_register_works_without_token(client):
    # /api/auth/register 白名单:POST 无 token 也能过。
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        resp = client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": "S3cret-pw"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["username"] == username
    finally:
        _cleanup(username, email)


def test_health_endpoint_works_without_token(client):
    # /api/health 白名单:GET 无 token 也能过。
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ── 写操作要求 JWT ────────────────────────────────────────────────


def test_write_without_token_returns_401(client):
    # 选一个非白名单的写端点。/api/auth/refresh 不在白名单吗?
    # —— 实际上 refresh 在白名单里(/api/auth/refresh)。改用一个一定不在白名单的
    # POST 端点。这里用 /api/auth/logout 做探针:它也不在白名单吗?
    # 注: logout 在白名单里。所以换一个确定不在白名单的 POST。
    # 用一个不存在的 /api/ POST 路径:FastAPI 会返回 404,但中间件在路由前执行,
    # 写操作无 token → 中间件先返回 401。这恰好验证中间件守卫写操作。
    resp = client.post("/api/some-nonexistent-write-endpoint", json={})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


def test_write_with_valid_token_passes_middleware(client):
    # 同一个不存在的写端点,带上有效 JWT → 中间件放行,FastAPI 路由层返回 404
    # (说明请求穿过了中间件到达路由)。
    username = _unique()
    email = f"{username}@example.com"
    _cleanup(username, email)
    try:
        token = _register_and_login(client, username, email, "S3cret-pw")
        resp = client.post(
            "/api/some-nonexistent-write-endpoint",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 中间件放行 → FastAPI 路由 404(证明 token 通过了中间件校验)
        assert resp.status_code == 404
    finally:
        _cleanup(username, email)


def test_write_with_invalid_token_returns_401(client):
    # 写操作携带无效 token → 中间件 401(不像 GET 那样静默放行)。
    resp = client.post(
        "/api/some-nonexistent-write-endpoint",
        json={},
        headers={"Authorization": "Bearer not.a.valid.token"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not authenticated"


# ── X-API-Key 运维通道(需临时配置 BACKEND_API_KEY)───────────────


def test_api_key_ops_channel_allows_write(client, monkeypatch):
    """BACKEND_API_KEY 配置后,X-API-Key 匹配的写请求放行(运维通道)。"""
    ops_key = "ops-test-key-" + _secrets.token_hex(8)
    # 中间件实例在 app 装配时已建好,_API_KEY 是模块级常量。临时改 app 上的
    # 中间件实例属性即可(不污染其它用例)。
    mw = _find_auth_middleware(main_module.app)
    assert mw is not None, "JWTAuthMiddleware 未在 app 中间件栈里找到"
    original_key = mw._api_key
    mw._api_key = ops_key
    try:
        resp = client.post(
            "/api/some-nonexistent-write-endpoint",
            json={},
            headers={"X-API-Key": ops_key},
        )
        # 运维通道放行 → 路由 404
        assert resp.status_code == 404
    finally:
        mw._api_key = original_key


def test_api_key_wrong_key_rejected(client):
    """BACKEND_API_KEY 配置后,错误的 X-API-Key 在写操作上被拒(401)。"""
    mw = _find_auth_middleware(main_module.app)
    assert mw is not None
    original_key = mw._api_key
    mw._api_key = "correct-ops-key-" + _secrets.token_hex(8)
    try:
        resp = client.post(
            "/api/some-nonexistent-write-endpoint",
            json={},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401
    finally:
        mw._api_key = original_key


def test_api_key_channel_does_not_inject_user_identity(client):
    """运维通道放行但不注入用户身份:/api/auth/me 读不到 user_id → 401。"""
    mw = _find_auth_middleware(main_module.app)
    assert mw is not None
    original_key = mw._api_key
    mw._api_key = "ops-key-" + _secrets.token_hex(8)
    try:
        resp = client.get(
            "/api/auth/me", headers={"X-API-Key": mw._api_key}
        )
        # 中间件放行(auth_method=api_key, user_id=None),端点读到无 user_id → 401
        assert resp.status_code == 401
    finally:
        mw._api_key = original_key


# ── 辅助 ──────────────────────────────────────────────────────────


def _find_auth_middleware(app):
    """遍历 Starlette 中间件栈,找到 JWTAuthMiddleware 实例。

    Starlette/FastAPI 把已注册中间件包成链(ServerErrorMiddleware → Trace →
    RateLimit → JWTAuth → CORS → ...),链头存在 ``app.middleware_stack`` 属性里。
    该属性是惰性构建的:首次 ``app.__call__`` 时才赋值(见 Starlette
    applications.py: ``__call__`` 里 ``if self.middleware_stack is None:
    self.middleware_stack = self.build_middleware_stack()``)。

    因此这里先确保 middleware_stack 已构建(与 ``__call__`` 用完全相同的赋值路径),
    再沿 ``.app`` 链向下找。这样拿到的实例就是 TestClient 实际请求经过的那一个,
    修改 ``mw._api_key`` 才会真正影响后续请求。

    注意: 不能直接调 ``build_middleware_stack()`` —— 它只返回一条新建链、不会赋值给
    ``self.middleware_stack``,导致拿到的实例与后续 ``__call__`` 用的不是同一个。
    """
    from backend.middleware.auth import JWTAuthMiddleware

    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()

    seen = set()
    node = app.middleware_stack
    depth = 0
    while node is not None and depth < 30:
        if id(node) in seen:
            break
        seen.add(id(node))
        if isinstance(node, JWTAuthMiddleware):
            return node
        nxt = getattr(node, "app", None)
        if nxt is node or nxt is None:
            break
        node = nxt
        depth += 1
    return None
