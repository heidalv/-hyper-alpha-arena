"""阶段2 auth 端点的请求 / 响应 schema(Task 2.2)。

JWT 模型(spec §5 / §7.2):access + refresh 都在响应 body 里返回(非 cookie)。
Electron 桌面端拿到后存入 safeStorage(Task 0.3 起的桌面壳负责)。

email 字段:本仓库依赖里已带 email-validator,因此优先用 ``EmailStr``;若环境里
未装(被精简依赖裁掉),降级为普通 ``str``,保证 import 不挂。
"""
from __future__ import annotations

try:
    from pydantic import BaseModel, EmailStr
    _HAS_EMAIL_VALIDATOR = True
except ImportError:  # pragma: no cover — 仅在缺 email-validator 时触发
    from pydantic import BaseModel
    EmailStr = str  # type: ignore[misc,assignment]
    _HAS_EMAIL_VALIDATOR = False


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr  # 若未装 email-validator 则退化为 str
    password: str


class LoginRequest(BaseModel):
    # 接受用户名或邮箱登录(端点里按是否含 "@" 分流)
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None = None
    tier: str = "free"
    role: str = "user"  # user | admin
    # VIP 共用 AI 选币（前端侧栏/页面门控）
    coin_select_enabled: bool = False
    coin_select_auto_follow: bool = False
    coin_select_default_session: str | None = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


# TokenResponse.user 是 UserOut;两个类都在本模块内定义,无需跨模块 forward ref,
# 但显式 rebuild 一次以容许未来把 UserOut 拆到独立模块后仍可用字符串引用。
TokenResponse.model_rebuild()
