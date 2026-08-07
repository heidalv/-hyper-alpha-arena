"""mlto_thesis add regime_suggestion column

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-05

v6 阶段2（S2-7）: regime 参数建议通道落库 —— 为 ``mlto_thesis`` 加
``regime_suggestion_json`` 列（校验后 applied dict 序列化），保证 LLM 输出的
regime 判定 + 参数档位建议（sl_multiplier/tp_trigger/trailing/addon_rhythm）
在重启后可从 DB 恢复，执行层 _llm_stops 继续消费。

存储选型：与 mid_view_json（0009）同构 —— 单 JSONB 列（PG）/ TEXT 兜底（SQLite）。
字段可能演进，JSONB 改模型即可，不需要再写迁移。

多库安全 / 幂等
----------------
与 0009 一致：env.py 把 upgrade 依次跑在 core/market/analytics 三个逻辑库上，
``mlto_thesis`` 仅存在于 analytics 库，用 ``inspector.has_table`` 守卫 no-op；
``op.add_column`` 非幂等，用 ``inspector.has_column`` 显式守卫跳过已存在列。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

# 与 0001_baseline / env.py 保持一致：确保 backend 父目录在 sys.path 上。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

# revision identifiers, used by Alembic
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0009 同构)
# ─────────────────────────────────────────────────────────────────
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


def _is_postgresql() -> bool:
    return _bind().dialect.name == "postgresql"


# ─────────────────────────────────────────────────────────────────
# upgrade / downgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # mlto_thesis 仅存在于 analytics 库；core/market bind 整体 no-op。
    if not _has_table("mlto_thesis"):
        return

    if _has_column("mlto_thesis", "regime_suggestion_json"):
        # 全新库 create_all 已建，或本迁移重跑 —— 跳过。
        return

    # PG 用 JSONB（可查询/可索引），其它方言（含 SQLite）兜底 TEXT。
    if _is_postgresql():
        col_type = sa.dialects.postgresql.JSONB(astext_type=sa.Text())
    else:
        col_type = sa.Text()

    op.add_column(
        "mlto_thesis",
        sa.Column("regime_suggestion_json", col_type, nullable=True),
    )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op（与 0009 保守语义一致）。
    pass
