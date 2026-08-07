"""add refresh_tokens table

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23

阶段2 auth 端点 (Task 2.2):
  - 新建 refresh_tokens 表,作为 JWT refresh token 的服务端 jti 账本。
  - 字段: id / user_id(FK users.id) / jti(unique) / revoked("true"/"false")
         / expires_at / created_at。
  - 用途: /api/auth/refresh 轮换(旧 jti revoked=true,签发新 jti);
          /api/auth/logout 撤销当前 jti。
  - 注意: 不要复用 user_repo.create_auth_session / verify_auth_session —— 它们
    引用未导入的 ``timezone`` (NameError bug,见 Task 2.1 备注)。本表走全新存储路径。

多库安全说明
------------
本仓库 alembic env.py 会把 upgrade/downgrade 依次跑在三套逻辑库
(core/market/analytics)上,而 refresh_tokens 只应存在于 core 库(与 users 同库,
user_id 引用 users.id)。这里复用 0002 的 inspector 守卫:当前 bind 上若没有
users 表,则整体跳过(market/analytics 是 no-op)——避免在 market/analytics 库
凭空造一张 refresh_tokens 表。

由于这是新建一张全新表(create_table + create_index 天然幂等性差),在 guard 通过
后仍用 ``sa.inspect(bind).has_table("refresh_tokens")`` 二次判定,已存在则跳过,
保证重跑安全。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

# 与 0001_baseline / 0002 / env.py 保持一致:确保 backend 父目录在 sys.path 上。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

# revision identifiers, used by Alembic
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    """当前 bind(对应某个逻辑库)上是否存在指定表。"""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table(name)


def upgrade() -> None:
    # refresh_tokens 依赖 users.id(外键),故只在存在 users 的库(= core)上创建。
    if not _has_table("users"):
        # market / analytics 库没有 users 表 —— 跳过,保持 no-op。
        return

    # 幂等:若 refresh_tokens 已存在(例如重跑)则跳过 create。
    if _has_table("refresh_tokens"):
        return

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.String(length=10), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_refresh_tokens_id", "refresh_tokens", ["id"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)


def downgrade() -> None:
    if not _has_table("users"):
        return
    if not _has_table("refresh_tokens"):
        return
    op.drop_index("ix_refresh_tokens_jti", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
