"""add admin_audit_logs table

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-23

阶段4 Task 4.3: 管理员操作审计日志表。

  - admin_audit_logs 是 **GLOBAL 表**(不带 tenant_id / 不挂 RLS 策略)。
    它记录"哪个 admin 对哪个 user 做了什么",访问控制完全由路由层
    ``require_admin`` 依赖兜住(role != admin → 403),非 admin 根本到不了这张表。
    所以无需(也不应)给它加 RLS policy —— 给 GLOBAL 审计表挂租户过滤反而会
    妨碍 admin 跨租户查看完整操作历史。

  - detail 列用 JSON 类型:PostgreSQL 自动映射为 JSONB(可索引/灵活查询),
    SQLite 映射为 TEXT(JSON 序列化)。两种方言下 ORM 都能透明读写,无需分支。

多库安全
--------
和 0002 / 0006 一样:env.py 会把 upgrade/downgrade 依次跑在三套逻辑库
(core/market/analytics),而 users 表(本表 FK 目标)只存在于 core 库。
用 inspector.has_table("users") 守卫:无 users 表的 bind 整体 no-op,
避免在 market/analytics 库凭空建出一张指向不存在表的 admin_audit_logs。

关于已 FORCE 的表(见 0005 说明)
-------------------------------
本迁移只 CREATE 一张全新的 GLOBAL 表,不改任何 FORCE 表,也不在 FORCE 表上
落 DML,故无需 SET app.is_admin。新表本身不挂 RLS(无 ENABLE ROWLEVEL 调用)。

幂等
----
op.create_table 在表已存在时会报错;这里用 inspector 显式判断,已存在则跳过。
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
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _users_exists() -> bool:
    """当前 bind(对应某个逻辑库)上是否存在 users 表。

    admin_audit_logs.admin_user_id FK 到 users.id,只能在 core 库(有 users 表)
    创建。market/analytics 库没有 users 表 → 跳过,保持 no-op。
    """
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table("users")


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        return insp.has_table(table_name)
    except Exception:
        return False


def upgrade() -> None:
    if not _users_exists():
        # market / analytics 库没有 users 表 —— 跳过,保持 no-op。
        return

    # 幂等:表已存在(部分回滚后重跑)则跳过 create_table。
    if _table_exists("admin_audit_logs"):
        return

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        # JSON 通用类型:PG→JSONB,SQLite→TEXT。与 models.py 的 Column(JSON) 对齐。
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("current_timestamp"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_logs_id", "admin_audit_logs", ["id"])
    op.create_index(
        "ix_admin_audit_logs_admin_user_id", "admin_audit_logs", ["admin_user_id"]
    )
    op.create_index(
        "ix_admin_audit_logs_target_user_id", "admin_audit_logs", ["target_user_id"]
    )


def downgrade() -> None:
    if not _users_exists():
        return
    if not _table_exists("admin_audit_logs"):
        return
    try:
        op.drop_index("ix_admin_audit_logs_target_user_id", table_name="admin_audit_logs")
    except Exception:
        pass
    try:
        op.drop_index("ix_admin_audit_logs_admin_user_id", table_name="admin_audit_logs")
    except Exception:
        pass
    try:
        op.drop_index("ix_admin_audit_logs_id", table_name="admin_audit_logs")
    except Exception:
        pass
    op.drop_table("admin_audit_logs")
