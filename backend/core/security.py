# backend/core/security.py
"""JWT 编解码入口。

密码哈希实现放在 ``backend.repositories.user_repo``(passlib bcrypt),
本模块只负责 access/refresh token 的签发与校验。

环境变量(均有 dev 默认值,生产必须覆盖 JWT_SECRET):
  - JWT_SECRET                     HS256 签名密钥
  - JWT_ALGORITHM                  默认 HS256
  - ACCESS_TOKEN_EXPIRE_MINUTES    access token 有效期(分钟),默认 15
  - REFRESH_TOKEN_EXPIRE_DAYS      refresh token 有效期(天),默认 7
"""
import os
import secrets as _secrets
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError

SECRET = os.getenv("JWT_SECRET", "dev-only-change-me-in-prod")
ALGO = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

__all__ = [
    "JWTError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]


def create_access_token(sub: str, tenant_id: int, tier: str, role: str = "user") -> str:
    """签发 access token。

    Claims:
      - sub:        用户 ID(字符串)
      - tenant_id:  租户 ID(阶段3 接入,当前可传 0/默认租户)
      - tier:       用户等级 free/pro/vip(来自 User.tier)
      - role:       角色(阶段4 admin bootstrap 接入,默认 user)
      - type:       "access"
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ACCESS_MIN)
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "tier": tier,
        "role": role,
        "exp": exp,
        "iat": now,
        "type": "access",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def create_refresh_token(sub: str) -> tuple[str, str]:
    """签发 refresh token。

    返回 ``(token, jti)``。jti 用于服务端撤销名单(阶段2 后续接入)。
    """
    jti = _secrets.token_hex(16)
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=REFRESH_DAYS)
    payload = {
        "sub": sub,
        "jti": jti,
        "exp": exp,
        "iat": now,
        "type": "refresh",
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO), jti


def decode_token(token: str) -> dict:
    """解码并校验 token(验签 + 过期)。失败抛 ``JWTError``。"""
    return jwt.decode(token, SECRET, algorithms=[ALGO])
