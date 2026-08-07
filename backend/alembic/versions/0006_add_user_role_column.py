"""add user role column + admin bootstrap

Revision ID: 0006
Revisies: 0005
Create Date: 2026-07-23

阶段4 Task 4.1 admin bootstrap:
  - users 加 role 列(user/admin),server_default='user',NOT NULL。
  - default 用户(现在运行的,id=1)升为 admin。
  - 兜底:若 default 不存在,把 id 最小的用户升 admin(保证至少有一个 admin,
    否则后续 RLS 短路 GUC 无人能触发)。

中间件(Task 4.2)从 access token 的 role claim 设 app.is_admin GUC;
Task 3.3 的 RLS policy 已含 `current_setting('app.is_admin', true) = 'on'`
OR 短路子句 —— 本迁移只负责落 role 列与首个 admin,不碰 RLS。

多库安全
--------
本仓库 env.py 把 upgrade/downgrade 依次跑在三套逻辑库(core/market/analytics),
而 users 表只存在于 core 库。用 inspector.has_table(...) 守卫:无 users 表的
bind 整体 no-op(同 0002 的处理方式)。

关于已 FORCE 的表(见 0005 说明)
-------------------------------
本迁移不改任何 FORCE 表,也没有 DML 落在 FORCE 表上,故无需 SET app.is_admin。
仅 users 表(0005 未对其 ENABLE RLS)被改,superuser/owner 任意写。

幂等
----
add_column 在列已存在时会报错;这里用 inspector 显式判断,已存在则跳过。
两条 UPDATE 天然幂等。
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
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _users_exists() -> bool:
    """当前 bind(对应某个逻辑库)上是否存在 users 表。"""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table("users")


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = {c["name"] for c in insp.get_columns(table_name)}
    except Exception:
        return False
    return column_name in cols


def upgrade() -> None:
    if not _users_exists():
        # market / analytics 库没有 users 表 —— 跳过,保持 no-op。
        return

    # 幂等:列已存在(部分回滚后重跑)则跳过 add_column。
    if not _has_column("users", "role"):
        op.add_column(
            "users",
            sa.Column("role", sa.String(length=20), server_default="user", nullable=False),
        )

    # default 用户升 admin("现在运行的这个")。
    op.execute("UPDATE users SET role='admin' WHERE username='default'")

    # 兜底:若 default 不存在(被删 / 改名),把 id 最小的用户升 admin,
    # 保证至少有一个 admin —— 否则 RLS 短路 GUC 无触发者。
    op.execute(
        "UPDATE users SET role='admin' "
        "WHERE id=(SELECT id FROM users ORDER BY id LIMIT 1) "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE role='admin')"
    )


def downgrade() -> None:
    if not _users_exists():
        return
    try:
        op.drop_column("users", "role")
    except Exception:
        pass
