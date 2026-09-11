"""mlto_thesis add prompt_version

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-07

[2026-09-07 OPRO 地基] 为 ``mlto_thesis`` 补 ``prompt_version``：
主脑 system prompt 短哈希。prompt 一改版本即变，平仓结算可按版本统计
胜率/净盈亏，为 Adaptive-OPRO 滚动优化提供评分依据。

多库安全 / 幂等：与 0022 一致，inspector 守卫，仅 analytics 库生效。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

revision = "0023"
down_revision = "0022"
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
        return column_name in {c["name"] for c in insp.get_columns(table_name)}
    except Exception:
        return False


def upgrade() -> None:
    if not _has_table("mlto_thesis"):
        return
    if not _has_column("mlto_thesis", "prompt_version"):
        op.add_column("mlto_thesis", sa.Column("prompt_version", sa.String(32), nullable=True))


def downgrade() -> None:
    pass
