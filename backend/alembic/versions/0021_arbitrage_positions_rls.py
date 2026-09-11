"""arbitrage_positions 补 tenant_id + ENABLE/FORCE RLS

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-04

背景（v3 p2-arb-infra）
----------------------
方案方向 5 明确点名：``arbitrage_positions`` 无租户隔离。0004/0005 给 rebate_positions /
arbitrage_paper_accounts 等加了 tenant_id + RLS，却漏了这张 V3 套利主仓位表——任意能
连上 DB 的租户会话都能读到全部套利仓。

做什么
------
1. 加 ``tenant_id INTEGER NULL``（可空 = 全局行，与 0005 策略「NULL 对所有租户可见」一致）
2. 历史行保持 NULL（本表无 account_id，无法按账户回填属主；系统级仓位继续共享）
3. ENABLE + FORCE ROW LEVEL SECURITY + tenant_isolation 策略（与 0005 V1 同款）

多库 / 幂等 / RLS
-----------------
- 仅 core 库有该表；market / analytics no-op
- 非 PostgreSQL 跳过
- FORCE 后普通 owner 角色的 DML 受策略约束；本迁移开头 SET LOCAL app.is_admin='on'
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "arbitrage_positions"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        print(f"[0021] skip non-postgres dialect={dialect}")
        return
    insp = sa.inspect(bind)
    if not insp.has_table(TABLE):
        print(f"[0021] skip: no table {TABLE}")
        return

    try:
        bind.execute(sa.text("SET LOCAL app.is_admin = 'on'"))
    except Exception:
        pass

    cols = {c["name"] for c in insp.get_columns(TABLE)}
    if "tenant_id" not in cols:
        op.add_column(TABLE, sa.Column("tenant_id", sa.Integer(), nullable=True))
        try:
            op.create_index("ix_arbitrage_positions_tenant_id", TABLE, ["tenant_id"])
        except Exception as e:
            print(f"[0021] index skip: {e}")

    # RLS（幂等）
    sp = bind.begin_nested()
    try:
        bind.execute(sa.text(f'ALTER TABLE "{TABLE}" ENABLE ROW LEVEL SECURITY;'))
        bind.execute(sa.text(f'ALTER TABLE "{TABLE}" FORCE ROW LEVEL SECURITY;'))
        bind.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{TABLE}";'))
        bind.execute(sa.text(f"""
            CREATE POLICY tenant_isolation ON "{TABLE}"
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int
                OR tenant_id IS NULL
                OR current_setting('app.is_admin', true) = 'on'
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int
                OR tenant_id IS NULL
                OR current_setting('app.is_admin', true) = 'on'
            );
        """))
        sp.commit()
        print(f"[0021] RLS enabled on {TABLE}")
    except Exception as e:
        sp.rollback()
        print(f"[0021] RLS skip {TABLE}: {e}")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    insp = sa.inspect(bind)
    if not insp.has_table(TABLE):
        return
    try:
        bind.execute(sa.text("SET LOCAL app.is_admin = 'on'"))
    except Exception:
        pass
    sp = bind.begin_nested()
    try:
        bind.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{TABLE}";'))
        bind.execute(sa.text(f'ALTER TABLE "{TABLE}" NO FORCE ROW LEVEL SECURITY;'))
        bind.execute(sa.text(f'ALTER TABLE "{TABLE}" DISABLE ROW LEVEL SECURITY;'))
        sp.commit()
    except Exception as e:
        sp.rollback()
        print(f"[0021] downgrade RLS skip: {e}")
    cols = {c["name"] for c in insp.get_columns(TABLE)}
    if "tenant_id" in cols:
        try:
            op.drop_index("ix_arbitrage_positions_tenant_id", TABLE)
        except Exception:
            pass
        op.drop_column(TABLE, "tenant_id")
