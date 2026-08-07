"""mlto_thesis add sl_pct/tp_pct columns

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-06

v6 阶段2（S2-9 审计项 7）：LLM 止损参数直通落库 —— 为 ``mlto_thesis`` 加
``sl_pct`` / ``tp_pct`` 两列（LLM exit_plan 解析结果，ATR 下限硬校验后使用）。

背景：``qual_layer`` 已把 LLM exit_plan 解析为 ThesisDTO.sl_pct/tp_pct，
``orchestrator._llm_stops`` 已消费（ATR floor + TP>=2xSL + structure 兜底），
但 DB 模型缺列导致 ``thesis_store._persist`` 无法落库 —— LLM 止损参数只在
内存，重启即失。本迁移补齐列，保证开仓止损参数在重启后可从 DB 恢复。

多库安全 / 幂等
----------------
与 0013 一致：env.py 把 upgrade 依次跑在 core/market/analytics 三个逻辑库上，
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
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0013 同构)
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


# ─────────────────────────────────────────────────────────────────
# upgrade / downgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # mlto_thesis 仅存在于 analytics 库；core/market bind 整体 no-op。
    if not _has_table("mlto_thesis"):
        return

    # 逐列幂等：已存在（全新库 create_all 已建，或本迁移重跑）→ 跳过。
    for col_name in ("sl_pct", "tp_pct"):
        if _has_column("mlto_thesis", col_name):
            continue
        op.add_column(
            "mlto_thesis",
            sa.Column(col_name, sa.Float(), nullable=True),
        )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op（与 0009/0013 保守语义一致）。
    pass
