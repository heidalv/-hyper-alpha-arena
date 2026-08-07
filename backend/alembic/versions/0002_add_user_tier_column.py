"""add user tier column

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23

阶段2 auth 基建:
  - users 表加 tier 列(free/pro/vip),server_default='free',NOT NULL。
  - 给 users.email 加唯一索引 ix_users_email_unique(email 列本身在 baseline 已存在,
    保持 nullable —— 阶段4 admin bootstrap 再按需收紧 NOT NULL)。

多库安全说明
------------
本仓库的 alembic env.py 会把 upgrade/downgrade 依次跑在三套逻辑库上
(core/market/analytics),而 users 表只存在于 core 库。因此这里用 inspector
检查当前 bind 上是否存在 users 表,不存在则整体跳过(对 market/analytics 是
no-op),避免 op.add_column 在没有 users 的库上报错导致整次 upgrade 失败。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

# 与 0001_baseline / env.py 保持一致:确保 backend 父目录在 sys.path 上。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

# revision identifiers, used by Alembic
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _users_exists() -> bool:
    """当前 bind(对应某个逻辑库)上是否存在 users 表。"""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table("users")


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        indexes = insp.get_indexes(table_name)
    except Exception:
        return False
    return any(ix.get("name") == index_name for ix in indexes)


def upgrade() -> None:
    if not _users_exists():
        # market / analytics 库没有 users 表 —— 跳过,保持 no-op。
        return

    op.add_column(
        "users",
        sa.Column("tier", sa.String(length=20), server_default="free", nullable=False),
    )

    # email 已存在(nullable);此处只补一个 unique index,便于登录态按 email 查重。
    # 幂等:若索引已存在(例如重跑)则跳过。
    if not _has_index("users", "ix_users_email_unique"):
        op.create_index("ix_users_email_unique", "users", ["email"], unique=True)


def downgrade() -> None:
    if not _users_exists():
        return
    if _has_index("users", "ix_users_email_unique"):
        op.drop_index("ix_users_email_unique", table_name="users")
    op.drop_column("users", "tier")
