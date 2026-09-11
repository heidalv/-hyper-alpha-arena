"""users.id 外键改为 ON DELETE CASCADE（14 张 user_id 表）

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-03

背景（2026-09-02 全量测试诊断）
--------------------------------
14 张表的 ``user_id`` 外键全部是 ``NO ACTION``，ORM ``relationship("User")`` 也没配
cascade —— 删除一个用户会直接撞外键失败。生产代码目前**没有**删除用户的路径
（``db.delete(user)`` 只出现在测试清理里），所以不是线上缺陷；但将来做"注销账号"
必然踩坑。用户已拍板：**全部加级联删除**（删用户连带清空其关联数据）。

本迁移改的 14 个约束（均为 ``user_id → users.id``）::

    accounts, ai_attribution_conversations, ai_prompt_conversations,
    ai_signal_conversations, alpha_assistant_conversations, atas_strategies,
    coin_select_adoptions, dashboard_layouts, exchange_credentials, refresh_tokens,
    user_auth_sessions, user_exchange_config, user_subscriptions, visual_strategies

**有意不改**：``admin_audit_logs.admin_user_id``（NOT NULL）。这是"谁做了管理操作"的
审计轨迹，删除管理员不应抹掉其操作记录 —— 保持 NO ACTION（有审计记录的管理员不可删，
这是合规上正确的阻断）。

**已知边界（需另行拍板）**：``accounts`` 自身还有 31 个子表外键（trades / positions /
orders / paper_* / full_auto_sessions …，除 risk_control_configs 外全是 NO ACTION）。
本迁移让 ``users → accounts`` 级联，但 ``accounts → 子表`` 未级联：有账户的用户删除仍会在
第二层被阻断。把交易历史一并级联属于不可逆的数据保留策略变更，未在本次决策范围内，
故不在此迁移中处理。

多库安全 / 幂等
----------------
- 仅 core 库（有 ``users`` 表）执行；market / analytics bind 整体 no-op。
- 非 PostgreSQL（如 SQLite 单测库）跳过：SQLite 不支持修改既有约束。
- 逐约束检查 ``information_schema.referential_constraints.delete_rule``，已是 CASCADE
  则跳过；约束名以库里实际名称为准，缺失则按 ``<table>_user_id_fkey`` 重建。
- 每条 ALTER 前 ``SET LOCAL lock_timeout``（默认 5s），避免在线系统上无限等锁；
  超时整段回滚，重跑即可。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)


# revision identifiers, used by Alembic
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

#: (table, column) —— 14 张 user_id 表
USER_FK_TABLES = (
    ("accounts", "user_id"),
    ("ai_attribution_conversations", "user_id"),
    ("ai_prompt_conversations", "user_id"),
    ("ai_signal_conversations", "user_id"),
    ("alpha_assistant_conversations", "user_id"),
    ("atas_strategies", "user_id"),
    ("coin_select_adoptions", "user_id"),
    ("dashboard_layouts", "user_id"),
    ("exchange_credentials", "user_id"),
    ("refresh_tokens", "user_id"),
    ("user_auth_sessions", "user_id"),
    ("user_exchange_config", "user_id"),
    ("user_subscriptions", "user_id"),
    ("visual_strategies", "user_id"),
)

_LOCK_TIMEOUT = os.getenv("ALEMBIC_LOCK_TIMEOUT", "5s")


def _bind():
    return op.get_bind()


def _is_pg() -> bool:
    try:
        return _bind().dialect.name == "postgresql"
    except Exception:
        return False


def _has_table(table_name: str) -> bool:
    insp = sa.inspect(_bind())
    try:
        return insp.has_table(table_name)
    except Exception:
        return False


def _fk_state(table: str, column: str):
    """返回 (constraint_name, delete_rule) 或 (None, None)。"""
    sql = sa.text(
        """
        SELECT tc.constraint_name, rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.referential_constraints rc
          ON tc.constraint_name = rc.constraint_name AND tc.constraint_schema = rc.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON rc.unique_constraint_name = ccu.constraint_name
         AND rc.unique_constraint_schema = ccu.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = current_schema()
          AND tc.table_name = :t AND kcu.column_name = :c
          AND ccu.table_name = 'users' AND ccu.column_name = 'id'
        LIMIT 1
        """
    )
    row = _bind().execute(sql, {"t": table, "c": column}).fetchone()
    if not row:
        return None, None
    return str(row[0]), str(row[1]).upper()


def _set_rule(table: str, column: str, rule: str) -> bool:
    """把 table.column → users.id 的外键改成指定 ON DELETE 规则；返回是否实际改动。"""
    name, current = _fk_state(table, column)
    if current == rule:
        return False
    cname = name or f"{table}_{column}_fkey"
    conn = _bind()
    conn.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    if name:
        conn.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"'))
    conn.execute(sa.text(
        f'ALTER TABLE "{table}" ADD CONSTRAINT "{cname}" '
        f'FOREIGN KEY ("{column}") REFERENCES users(id) ON DELETE {rule}'
    ))
    return True


def _apply(rule: str) -> None:
    if not _is_pg() or not _has_table("users"):
        return
    changed = []
    for table, column in USER_FK_TABLES:
        if not _has_table(table):
            continue
        if _set_rule(table, column, rule):
            changed.append(table)
    print(f"[0019] users.id 外键 ON DELETE {rule}: 改动 {len(changed)} 张 -> {changed}")


def upgrade() -> None:
    _apply("CASCADE")


def downgrade() -> None:
    _apply("NO ACTION")
