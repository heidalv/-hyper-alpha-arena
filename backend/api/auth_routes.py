"""阶段2 auth 端点: /api/auth/register | login | refresh | logout | me(Task 2.2)。

认证模型(spec §5 / §7.2)
------------------------
JWT,access + refresh 都在响应 **body** 里返回(不是 HttpOnly cookie)。Electron
桌面端负责把它们存到 safeStorage;服务端不下发 cookie。

refresh token 服务端账本
-----------------------
不使用 user_repo.create_auth_session / verify_auth_session —— 它们有 ``timezone``
NameError bug(Task 2.1 备注),且是旧的 opaque session token 体系。本模块走全新的
``RefreshToken`` 表(存 jti + revoked + expires_at):
  - /refresh 做 **轮换**:旧 jti 标记 revoked="true",签发一对新 token(新 jti)。
  - /logout 撤销当前 jti(idempotent,token 解不开也直接返回 logged out)。

依赖关系
-------
  - backend.core.security: create_access_token / create_refresh_token / decode_token
  - backend.repositories.user_repo: create_user / get_user_by_username /
    get_user_by_email / _hash_password / _verify_password(passlib bcrypt)
  - User.tier 列(Task 2.1):free/pro/vip,access token claim 里带上
"""
from __future__ import annotations

import os
import threading
import time as _time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.core.security import (
    REFRESH_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from backend.database.connection import get_db
from backend.database.models import RefreshToken, User
from backend.repositories.user_repo import (
    _verify_password,
    create_user,
    get_user_by_email,
    get_user_by_username,
)
from backend.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# refresh 轮换幂等缓存（消除多客户端并发刷新的 401 风暴 + refresh_tokens 爆炸）
# ---------------------------------------------------------------------------
# 背景：access token 15min 过期，多窗口（Electron 桌面端 + 浏览器）共享同一份
# localStorage 凭证。任一窗口先刷新一次就把旧 jti 轮换掉，其余窗口仍持旧 jti →
# 全部被判 revoked → 401 → 前端 tryRefresh 反复登出/重试 → 观测到 1000+ 次
# /auth/refresh 401，且每次成功轮换都新增一行 refresh_tokens（爆炸到上千行）。
#
# 修复（业界标准的 "refresh token rotation reuse grace"）：一次成功轮换后，把
# parent_jti → 刚签发的子令牌对 缓存一个很短的 TTL（默认 25s）。宽限窗内，携带
# 同一 parent refresh token 的并发/重复请求直接返回 **同一份** 子令牌（幂等），
# 而不是把彼此判为失效。这样并发客户端会收敛到同一条最新令牌链，风暴消失，
# 也不会每次都新签一对（不再爆炸）。窗口外重用旧令牌仍然 401，撤销语义基本不变。
#
# 安全权衡：宽限窗内，持有"刚被轮换掉"的 parent token 者可再拿到一次子令牌——
# 这与原轮换的信任模型一致（拿到 parent 即视为合法持有者），窗口极短（默认 25s），
# 影响极小。可用 AUTH_REFRESH_ROTATION_GRACE_SEC 调整；设为 0 则完全关闭本机制。
try:
    _ROTATION_GRACE_SEC = float(os.environ.get("AUTH_REFRESH_ROTATION_GRACE_SEC", "25") or 25.0)
except (TypeError, ValueError):
    _ROTATION_GRACE_SEC = 25.0

_rotation_cache: "dict[str, tuple[float, TokenResponse]]" = {}
_rotation_lock = threading.Lock()


def _prune_rotation_cache(now: float) -> None:
    """惰性清理：删除已过宽限期的条目（在持锁上下文内调用）。"""
    stale = [k for k, (exp, _) in _rotation_cache.items() if exp <= now]
    for k in stale:
        _rotation_cache.pop(k, None)


def remember_rotation(parent_jti: str, resp: "TokenResponse") -> None:
    """记住一次成功轮换：parent_jti → 子令牌对，缓存 _ROTATION_GRACE_SEC 秒。"""
    if not parent_jti or _ROTATION_GRACE_SEC <= 0:
        return
    now = _time.monotonic()
    with _rotation_lock:
        _prune_rotation_cache(now)
        _rotation_cache[parent_jti] = (now + _ROTATION_GRACE_SEC, resp)


def get_rotation(parent_jti: str) -> "TokenResponse | None":
    """宽限窗内命中则返回上次为该 parent 签发的同一份子令牌，否则 None。"""
    if not parent_jti or _ROTATION_GRACE_SEC <= 0:
        return None
    now = _time.monotonic()
    with _rotation_lock:
        _prune_rotation_cache(now)
        item = _rotation_cache.get(parent_jti)
        return item[1] if item is not None else None


def _clear_rotation_cache() -> None:
    """测试辅助：清空幂等缓存，模拟宽限期已过。"""
    with _rotation_lock:
        _rotation_cache.clear()


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """注册新用户并直接签发首对 token(省一次 login 往返)。"""
    if get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="username taken")
    if req.email and get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="email registered")

    user = create_user(db, req.username, req.email, req.password)
    # Task 2.1 的 server_default='free' 应已兜底;这里再保一层以防迁移未跑。
    user.tier = user.tier or "free"
    db.commit()
    db.refresh(user)
    return _issue_tokens(db, user)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """用户名或邮箱 + 密码登录。"""
    user = get_user_by_username(db, req.username)
    if user is None and "@" in req.username:
        # 用户名里带 @ 视为邮箱登录
        user = get_user_by_email(db, req.username)
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """用 refresh token 换一对新 token。实现轮换:旧 jti 立即撤销。"""
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        # 验签失败 / 过期 / 格式错 —— 统一 401,不泄露具体原因
        raise HTTPException(status_code=401, detail="invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="not a refresh token")

    # 恢复租户身份（关键修复）：/api/auth/refresh 是中间件白名单路径，中间件
    # 不会为其设置 RLS 上下文。而 refresh_tokens 表被 FORCE RLS（租户键 user_id），
    # 若不先 set_request_identity，下面的查询会被 RLS fail-closed 隐藏，
    # 导致刚签发的有效 refresh token 被误判为 revoked → 前端刷新失败 → 用户被登出。
    # 注意顺序：必须在任何 DB 查询之前调用。SET LOCAL 由 begin 钩子在事务开始时
    # 读取 ContextVar 设置；若先执行 db.query()（触发 autobegin）再 set 身份，
    # 当前事务的 GUC 就不会再设置，RLS 仍会隐藏记录。
    from backend.core.tenant import set_request_identity

    _sub = payload.get("sub")
    try:
        _uid = int(_sub) if _sub is not None else None
    except (TypeError, ValueError):
        _uid = None
    if _uid is None:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    set_request_identity(_uid, role="user")

    user = db.query(User).filter(User.id == _uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="user gone")

    jti = payload.get("jti")
    record = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if not record:
        raise HTTPException(status_code=401, detail="refresh token revoked")
    if record.revoked == "true":
        # 幂等宽限：若该 jti 是"刚刚被本服务轮换掉"的（多窗口/重复刷新竞态），
        # 直接返回上次为它签发的同一份子令牌，避免把并发客户端判为失效而引发
        # 401 风暴与反复登出。窗口外（默认 25s）命中不到 → 仍按撤销处理返回 401。
        cached = get_rotation(jti)
        if cached is not None:
            return cached
        raise HTTPException(status_code=401, detail="refresh token revoked")
    if record.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
        # 兜底:DB 侧过期判定(decode_token 已校验 JWT exp,这里防 DB 时间漂移)
        record.revoked = "true"
        db.commit()
        raise HTTPException(status_code=401, detail="refresh token expired")

    # 轮换:撤销当前 jti,再签发新对(新 jti)，并把 parent_jti→子令牌记入宽限缓存。
    record.revoked = "true"
    db.commit()
    resp = _issue_tokens(db, user)
    remember_rotation(jti, resp)
    return resp


@router.post("/logout")
def logout(req: RefreshRequest, db: Session = Depends(get_db)):
    """撤销当前 refresh token。幂等:token 已失效 / 解不开也算 logged out。"""
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        return {"detail": "logged out"}  # idempotent
    if payload.get("type") != "refresh":
        return {"detail": "logged out"}

    # 与 /refresh 同因：白名单路径无 RLS 上下文，且必须先于任何 DB 查询恢复身份，
    # 否则事务 begin 钩子读到空 ContextVar，GUC 不会设置，记录会被 RLS 隐藏而撤销不掉。
    from backend.core.tenant import set_request_identity

    _sub = payload.get("sub")
    try:
        _uid = int(_sub) if _sub is not None else None
    except (TypeError, ValueError):
        _uid = None
    if _uid is not None:
        set_request_identity(_uid, role="user")

    jti = payload.get("jti")
    record = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if record:
        record.revoked = "true"
        db.commit()
    return {"detail": "logged out"}


@router.get("/me", response_model=UserOut)
def me(request: Request, db: Session = Depends(get_db)):
    """返回当前登录用户(身份由 JWTAuthMiddleware 从 JWT 注入到 request.state)。

    身份来源链:
      - JWTAuthMiddleware 解 Authorization Bearer → decode_token → 写
        ``request.state.user_id`` (sub claim,字符串形式用户 ID)。
      - 本端点读 ``request.state.user_id`` 定位 User 行。

    中间件对 GET 是开放的(无效/缺失 token 不拦截,只不注入身份),所以这里
    显式判 ``user_id`` 是否存在:无身份 → 401。
    """
    state = request.scope.get("state", {})
    user_id = state.get("user_id")
    if not user_id:
        # 未登录(无 token / 无效 token / 运维 api_key 通道)。
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="not authenticated")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    def _flag(v) -> bool:
        return str(v or "false").lower() in ("true", "1", "yes", "on")

    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        tier=user.tier or "free",
        role=getattr(user, "role", None) or "user",
        coin_select_enabled=_flag(getattr(user, "coin_select_enabled", None)),
        coin_select_auto_follow=_flag(getattr(user, "coin_select_auto_follow", None)),
        coin_select_default_session=getattr(user, "coin_select_default_session", None),
    )


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _issue_tokens(db: Session, user: User) -> TokenResponse:
    """签发 access + refresh 并把 refresh 的 jti 落库(账本)。

    tenant_id 暂时取 user.id;阶段3 接入真正的租户隔离后这里换成 user.tenant_id。

    重要:落库前必须 set_request_identity(user.id),否则 FORCE RLS 下
    ``refresh_tokens`` 插入会因 app.tenant_id 与 user_id 不一致而 500
    (尤其 AUTH_LOCAL_TENANT=1 时新建用户永远写不进账本)。
    """
    from backend.core.tenant import set_request_identity

    tier = user.tier or "free"
    # 阶段4 admin bootstrap:role 从 DB 读出写入 access token。default 用户在
    # 迁移 0006 被升为 admin,登录后 token role=admin;中间件(Task 4.2)据此
    # 设 app.is_admin GUC → RLS 短路。getattr 兜底:迁移未跑 / 旧库无 role 列时
    # 退回 "user",不影响普通登录。
    role = getattr(user, "role", None) or "user"
    set_request_identity(int(user.id), role=role)

    # 结束可能残留的事务,让下一次 autobegin 带上正确的 tenant GUC
    try:
        if db.in_transaction():
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    access = create_access_token(sub=str(user.id), tenant_id=user.id, tier=tier, role=role)
    refresh_tok, jti = create_refresh_token(sub=str(user.id))

    rt = RefreshToken(
        user_id=user.id,
        jti=jti,
        revoked="false",
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS),
    )
    db.add(rt)
    db.commit()

    return TokenResponse(
        access_token=access,
        refresh_token=refresh_tok,
        user=UserOut(
            id=user.id,
            username=user.username,
            email=user.email,
            tier=tier,
            role=role,
            coin_select_enabled=str(getattr(user, "coin_select_enabled", None) or "false").lower()
            in ("true", "1", "yes", "on"),
            coin_select_auto_follow=str(getattr(user, "coin_select_auto_follow", None) or "false").lower()
            in ("true", "1", "yes", "on"),
            coin_select_default_session=getattr(user, "coin_select_default_session", None),
        ),
    )
