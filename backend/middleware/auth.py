"""
JWT Authentication Middleware

阶段2 Task 2.3: 校验 JWT access token 并把身份(user_id/tenant_id/tier/role)注入
``scope["state"]``,下游路由通过 ``request.state`` 即可读取当前用户。

设计要点
--------
1. 双通道鉴权:
   - **JWT (主)**: ``Authorization: Bearer <access_token>`` 头。
     成功解码后 ``scope["state"]`` 写入 ``user_id`` / ``tenant_id`` / ``tier`` /
     ``role`` / ``auth_method="jwt"``。
   - **X-API-Key (运维通道)**: ``BACKEND_API_KEY`` 环境变量配置后,匹配
     ``X-API-Key`` 头的请求放行,但 ``auth_method="api_key"``,身份字段为 None
     (运维场景,无租户上下文)。供 ops / 健康检查 / 迁移过渡期使用。

2. 策略(阶段2 → 阶段3 渐进收紧):
   - **写操作** (POST/PUT/DELETE/PATCH,非白名单的 /api/ 路径): 必须有有效 JWT
     或有效 X-API-Key,否则 401。
   - **危险读** (``_DANGEROUS_GET_PREFIXES`` 下的 GET,如 ``/api/llm-configs/``):
     同样要求有效凭证 —— 这些路径在 GET 里暴露机密(LLM 明文 key 等),不能开放。
   - **普通读操作** (其它 GET): 暂时全部开放(不破坏现有读密集型用法);若携带有效
     JWT,仍注入身份;无效 JWT 在普通 GET 上静默跳过(保持读开放)。
   - 阶段3 接入租户隔离后,所有 GET 都会要求有效 JWT 才能拿到 tenant 上下文。

3. 白名单(完全跳过鉴权,不注入身份):
   - ``/api/auth/login`` ``/api/auth/register`` ``/api/auth/refresh``
     ``/api/auth/logout`` —— 登录/注册/刷新本身需要匿名可访问。
   - ``/api/health`` —— 健康探针。
   - 文档/静态/SPA catch-all 等 ``/api/`` 之外的路径。

4. 纯 ASGI 中间件(沿用旧 APIKeyMiddleware 的实现方式,避免
   BaseHTTPMiddleware 在多线程高 GIL 竞争下导致的请求处理延迟)。
   ``scope.setdefault("state", {})`` 后 ``scope["state"].update(...)``,路由侧
   ``request.state.user_id`` 即可读取(Starlette Request.state 包同一个 dict
   引用)。
"""

from __future__ import annotations

import logging
import os

from starlette.responses import JSONResponse

from backend.core.security import JWTError, decode_token
from backend.core.tenant import set_request_identity, clear_request_identity

logger = logging.getLogger(__name__)

# 运维通道密钥。未配置则 X-API-Key 通道关闭(但 JWT 通道仍可独立工作)。
_API_KEY: str | None = os.getenv("BACKEND_API_KEY")

# 本地单租户模式:配置 AUTH_LOCAL_TENANT=<user_id> 后,仅本机回环请求在
# 未携带 JWT / X-API-Key 时按该租户身份放行(用于本地单用户部署,
# 配合前端无登录页的 frontend-next;生产环境请勿配置)。
_LOCAL_TENANT: int | None = None
_LOCAL_TENANT_RAW = os.getenv("AUTH_LOCAL_TENANT", "").strip()
if _LOCAL_TENANT_RAW:
    try:
        _LOCAL_TENANT = int(_LOCAL_TENANT_RAW)
    except ValueError:
        _LOCAL_TENANT = None

# 完全豁免鉴权的精确 /api/ 路径(登录/注册/刷新/登出/健康探针)。
# 这些端点要么是匿名入口,要么是 ops 健康检查,不需要身份。
# 仅匿名入口与健康检查。模拟盘/策略写操作必须带 JWT，否则无法做租户隔离。
_AUTH_WHITELIST: frozenset[str] = frozenset({
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/health",
})

# /api/ 之外的路径前缀(websocket / 文档 / 旧静态资源),一律放行,不注入身份。
# 注: 阶段0 已移除前端静态托管,这里保留是为了 /docs /ws /openapi.json 等。
_NON_API_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static/",
    "/assets/",
    "/ws",
    "/auth-config.json",
    "/arena-updates/",  # 桌面 EXE 自动更新静态目录（无需登录）
)

# 根路径
_EXEMPT_PATHS: frozenset[str] = frozenset({"/"})

# 写操作方法集合(POST/PUT/DELETE/PATCH),这些方法在 /api/ 下要求有效凭证。
_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "DELETE", "PATCH"})

# 即使是 GET 也要鉴权的「危险读」前缀 —— 这些路径会在 GET body/字段里暴露机密
# (例如 LLM provider 的明文 API key)。阶段3 把所有 GET 都收紧后,这个清单就
# 退化成全集的一个子集,不再需要单独维护。现在保留是为了不破坏既有安全边界。
# 即使是 GET 也要鉴权的「危险读」前缀 —— 这些路径会在 GET body/字段里暴露机密
# (例如 LLM provider 的明文 API key)。也包含租户私有配置列表。
_DANGEROUS_GET_PREFIXES: tuple[str, ...] = (
    "/api/llm-configs",
    "/api/exchange/credentials",
    "/api/users/exchange-config",
    "/api/factors/discovered",
)


def _requires_auth(path: str, method: str) -> bool:
    """该请求是否必须持有有效凭证(JWT 或 X-API-Key)。

    - 写操作 (POST/PUT/DELETE/PATCH): 总是要求。
    - 危险读 GET (命中 ``_DANGEROUS_GET_PREFIXES``): 要求(防机密泄露)。
    - 其它 GET: 不要求(阶段2 保持读开放;phase3 租户隔离时收紧)。
    """
    if method in _WRITE_METHODS:
        return True
    if method == "GET":
        return any(path.startswith(p) for p in _DANGEROUS_GET_PREFIXES)
    return False


class JWTAuthMiddleware:
    """纯 ASGI 中间件: JWT 校验 + 身份注入,保留 X-API-Key 运维通道。

    流程(每个 HTTP 请求):
      1. 非 http(如 websocket)或不在 /api/ 下 → 直接放行。
      2. 命中白名单(login/register/refresh/logout/health)或豁免前缀 → 放行。
      3. 读 Authorization 头:
         - Bearer token 存在 → decode_token;成功注入身份,失败按 ``_requires_auth``
           决定(写操作/危险读 GET → 401,普通 GET 静默跳过保持读开放)。
         - 否则读 X-API-Key:匹配 BACKEND_API_KEY → 注入 auth_method="api_key"
           (身份 None),放行。
      4. 无任何凭证:
         - 写操作 / 危险读 GET → 401(见 ``_requires_auth``)。
         - 普通 GET → 放行(不注入身份,phase3 收紧)。
    """

    def __init__(self, app):
        self.app = app
        self._api_key = _API_KEY
        if self._api_key:
            logger.info("[Auth] JWT 中间件已启用;BACKEND_API_KEY 运维通道 ENABLED")
        else:
            logger.warning(
                "[Auth] JWT 中间件已启用;BACKEND_API_KEY 未配置 — "
                "仅 JWT 鉴权生效,运维 X-API-Key 通道关闭"
            )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # 非 /api/ 路径(websocket / docs / 等)直接放行,不注入身份。
        # (auth-config.json / openapi.json / docs / ws / 静态资源等)
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        # /api/ 下但命中白名单(login/register/refresh/logout/health)→ 放行。
        # [2026-07-30] 白名单同时支持前缀匹配（如 /api/strategy-config/daily-cap/* ）
        if path in _AUTH_WHITELIST or any(path.startswith(w) for w in _AUTH_WHITELIST):
            await self.app(scope, receive, send)
            return

        # 豁免根路径(理论上 / 不在 /api/ 下,这里兜底防漏)。
        if path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        # ── 解析请求头 ──────────────────────────────────────────
        authorization: str | None = None
        x_api_key: str | None = None
        for header_name, header_value in scope.get("headers", []):
            try:
                name = header_name.decode("latin-1").lower()
            except Exception:
                continue
            if name == "authorization" and authorization is None:
                try:
                    authorization = header_value.decode("latin-1")
                except Exception:
                    authorization = None
            elif name == "x-api-key" and x_api_key is None:
                try:
                    x_api_key = header_value.decode("latin-1")
                except Exception:
                    x_api_key = None

        # 确保 scope["state"] 存在(Starlette 在首次访问 request.state 时才会
        # setdefault,但中间件比路由早执行,主动建好 dict 后路由侧就能直接读到)。
        state = scope.setdefault("state", {})

        # ── 阶段3:先清空租户 ContextVar ────────────────────────────
        # 防御性清理:虽然 ContextVar 理论上随请求上下文自然隔离,但显式清零
        # 确保即使 ASGI 服务器复用协程对象也不会继承上一个请求的租户身份。
        # 后续 JWT 成功分支会重新 set_request_identity() 写入正确身份。
        clear_request_identity()

        # ── 通道 1: JWT Bearer token ────────────────────────────
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            try:
                payload = decode_token(token)
            except JWTError:
                # 验签/过期/格式错。
                # 需鉴权的请求(写操作 / 危险读 GET)直接 401;
                # 普通读 GET 静默放行(无效 token 不阻断读,phase3 收紧)。
                if _requires_auth(path, method):
                    logger.warning(
                        "[Auth] Invalid/expired JWT on protected route: method=%s path=%s",
                        method, path,
                    )
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "Not authenticated"},
                    )
                    await response(scope, receive, send)
                    return
                # 普通 GET: 无效 token 视同没带 token,读路径放行。
            except Exception as _e:
                # decode_token 自身异常(理论上不应发生,JWTError 已覆盖)。
                logger.debug("[Auth] decode_token unexpected error: %s", _e)
                if _requires_auth(path, method):
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "Not authenticated"},
                    )
                    await response(scope, receive, send)
                    return
            else:
                # JWT 校验通过 → 注入身份。access token 才算身份来源;
                # refresh token 不应作为请求身份凭证(只能用于 /refresh)。
                if payload.get("type") == "access":
                    sub = payload.get("sub")
                    # sub 是用户 ID 字符串;保留原样,路由侧按需 int()。
                    state["user_id"] = sub
                    state["tenant_id"] = payload.get("tenant_id")
                    state["tier"] = payload.get("tier")
                    state["role"] = payload.get("role")
                    state["auth_method"] = "jwt"
                    # ── 阶段3:注入租户身份到 ContextVar ────────────────
                    # after_begin/begin 钩子会读这个 ContextVar 在每个事务
                    # 开始时设 SET LOCAL app.tenant_id(防 521 处 commit 下
                    # GUC 失效的致命陷阱)。tenant_id 来自 JWT claim(int),
                    # 阶段3 当前 users.id 即 tenant_id。
                    jwt_tenant_id = payload.get("tenant_id")
                    jwt_role = payload.get("role") or "user"
                    try:
                        # JWT claim 可能是 int 或可转 int 的值;非法/None → None
                        tid_int = int(jwt_tenant_id) if jwt_tenant_id is not None else None
                    except (TypeError, ValueError):
                        tid_int = None
                    set_request_identity(tenant_id=tid_int, role=jwt_role)
                    await self.app(scope, receive, send)
                    return
                # type != "access"(例如误用 refresh token 做 Bearer):
                # 需鉴权则拒,普通 GET 放行(同无效 token 策略)。
                if _requires_auth(path, method):
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "Not authenticated"},
                    )
                    await response(scope, receive, send)
                    return

            # 到这里:JWT 解析过但未注入身份(无效 token / 非 access type)。
            # 继续往下走(可能命中 X-API-Key 运维通道,或按方法决定)。

        # ── 通道 2: X-API-Key 运维通道 ─────────────────────────
        if self._api_key and x_api_key == self._api_key:
            # 运维凭证: 放行,标记 auth_method="api_key",身份字段 None
            # (运维无租户/用户上下文)。下游若需 user_id/tenant_id 会拿到 None,
            # 应自行降级处理。
            state["user_id"] = None
            state["tenant_id"] = None
            state["tier"] = None
            state["role"] = None
            state["auth_method"] = "api_key"
            await self.app(scope, receive, send)
            return

        # ── 通道 2.5: 本地单租户模式(仅回环地址生效) ──────────
        if _LOCAL_TENANT is not None:
            client = scope.get("client")
            client_host = client[0] if client else ""
            if client_host in ("127.0.0.1", "::1"):
                state["user_id"] = str(_LOCAL_TENANT)
                state["tenant_id"] = _LOCAL_TENANT
                state["tier"] = "local"
                # [2026-08-17] 本地单租户模式以 admin 身份放行：单用户部署下该用户拥有全部
                # 数据，后台自动交易循环写盘时 ContextVar 无租户、落 tenant_id=1（见
                # connection.py _auto_fill_tenant_id 默认值），而 HTTP 请求落 tenant_id=该用户，
                # 导致同一账户持仓被拆到多个 tenant_id。若本地模式按 role=user 只透传单一
                # tenant_id，RLS 会把后台循环写的那部分持仓全部隐藏 → 前端持仓面板空/冻结。
                # 设 is_admin=True 走 RLS 短路，保证单用户能看到自己账户下的全部持仓。
                state["role"] = "admin"
                state["auth_method"] = "local"
                set_request_identity(tenant_id=_LOCAL_TENANT, role="admin")
                await self.app(scope, receive, send)
                return

        # ── 通道 3: 无凭证 ─────────────────────────────────────
        if _requires_auth(path, method):
            # 写操作 / 危险读 GET 要求有效 JWT 或 X-API-Key,缺失 → 401。
            logger.warning(
                "[Auth] Unauthenticated access blocked: method=%s path=%s",
                method, path,
            )
            response = JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )
            await response(scope, receive, send)
            return

        # 普通读 GET 且无凭证:放行(phase2 保持读开放;不注入身份)。
        await self.app(scope, receive, send)
