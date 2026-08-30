"""paper_positions MAE columns

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-30

PostFill Position Agent Phase 1-2：``paper_positions`` 增加 MAE 谷值两列
（与 peak_unrealized_pnl / peak_pnl_pct 对称）：

- ``trough_unrealized_pnl`` FLOAT NOT NULL DEFAULT 0.0 —— 持仓期最大浮亏（美元）
- ``trough_pnl_pct`` FLOAT NOT NULL DEFAULT 0.0 —— 持仓期最大浮亏（价格%）

用途：平仓遥测（position_exit_events.metadata_json）与因子进化闭环的
亏损归因——区分"止损太紧"（MAE 浅仍被 SL 扫出）与"方向错"（MAE 深）。

多库安全
--------
同 0011：paper_positions 仅存在于 core 库。无 accounts 表的 bind
（market / analytics）整体 no-op。

幂等
----
先 inspect 现有列，已存在则跳过；SQLite / PG 均安全。
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
revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _has_table(table_name: str) -> bool:
    insp = sa.inspect(_bind())
    try:
        return insp.has_table(table_name)
    except Exception:
        return False


def _has_column(table_name: str, column_name: str) -> bool:
    insp = sa.inspect(_bind())
    try:
        cols = {c["name"] for c in insp.get_columns(table_name)}
        return column_name in cols
    except Exception:
        return False


def upgrade() -> None:
    if not _has_table("accounts"):  # 仅 core 库有 accounts
        return
    if not _has_table("paper_positions"):
        return

    if not _has_column("paper_positions", "trough_unrealized_pnl"):
        with op.batch_alter_table("paper_positions") as batch:
            batch.add_column(
                sa.Column("trough_unrealized_pnl", sa.Float(), nullable=False, server_default="0")
            )
    if not _has_column("paper_positions", "trough_pnl_pct"):
        with op.batch_alter_table("paper_positions") as batch:
            batch.add_column(
                sa.Column("trough_pnl_pct", sa.Float(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    # 收口迁移：downgrade no-op（列无害且可能已承载数据，盲目 drop 有丢失风险）。
    pass
