"""mlto_thesis add brain columns

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-05

中长线 LLM 主脑：为 ``mlto_thesis`` 补 ``recommend_open`` / ``should_close`` /
``accepted`` / ``expires_at`` / ``analysis_run_id``。

DTO 里这几个字段早就有，表上没有 → 重启后缓存空、否决闸 fail-open。
本迁移只加列，不改旧行语义（accepted=0、should_close=0）。

多库安全 / 幂等
----------------
与 0017 一致：env.py 把 upgrade 依次跑在 core/market/analytics 三个逻辑库上，
``mlto_thesis`` 仅存在于 analytics 库，用 ``inspector.has_table`` 守卫 no-op；
``op.add_column`` 非幂等，用 ``inspector.has_column`` 显式守卫跳过已存在列。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

revision = "0022"
down_revision = "0021"
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

    cols = (
        ("recommend_open", sa.Column("recommend_open", sa.Integer(), nullable=True)),
        ("should_close", sa.Column("should_close", sa.Integer(), nullable=False, server_default="0")),
        ("accepted", sa.Column("accepted", sa.Integer(), nullable=False, server_default="0")),
        ("expires_at", sa.Column("expires_at", sa.TIMESTAMP(), nullable=True)),
        ("analysis_run_id", sa.Column("analysis_run_id", sa.String(64), nullable=True)),
    )
    for col_name, col in cols:
        if _has_column("mlto_thesis", col_name):
            continue
        op.add_column("mlto_thesis", col)


def downgrade() -> None:
    pass
