"""VIP 共用 AI 选币 — 平台扫描批次表

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _bind():
    return op.get_bind()


def _has_table(name: str) -> bool:
    try:
        return sa.inspect(_bind()).has_table(name)
    except Exception:
        return False


def _has_column(table: str, column: str) -> bool:
    try:
        cols = {c["name"] for c in sa.inspect(_bind()).get_columns(table)}
        return column in cols
    except Exception:
        return False


def upgrade() -> None:
    if not _has_table("users"):
        return  # market/analytics bind

    if _has_table("users") and not _has_column("users", "coin_select_enabled"):
        op.add_column("users", sa.Column("coin_select_enabled", sa.String(10), server_default="false", nullable=False))
        op.add_column("users", sa.Column("coin_select_auto_follow", sa.String(10), server_default="false", nullable=False))
        op.add_column("users", sa.Column("coin_select_default_session", sa.String(64), nullable=True))

    if _has_table("accounts") and not _has_column("accounts", "ai_coin_select_enabled"):
        op.add_column(
            "accounts",
            sa.Column("ai_coin_select_enabled", sa.String(10), server_default="false", nullable=False),
        )

    from backend.database.models import CoinSelectAdoption, CoinSelectCandidate, CoinSelectScan
    from backend.database.connection import Base

    eng = _bind()
    Base.metadata.create_all(
        eng,
        tables=[
            CoinSelectScan.__table__,
            CoinSelectCandidate.__table__,
            CoinSelectAdoption.__table__,
        ],
    )


def downgrade() -> None:
    if not _has_table("users"):
        return
    for t in ("coin_select_adoptions", "coin_select_candidates", "coin_select_scans"):
        if _has_table(t):
            op.drop_table(t)
