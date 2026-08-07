"""autocoin add factor_snapshot_json column

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-05

v6 阶段2（S2-9）: 选币因子 IC 加权 —— 为 ``auto_coin_selections`` 加
``factor_snapshot_json`` 列，在 injected 时写入当次评分的因子快照
（五维分数 vol/trend/mom/vola/fund + 链上分数 flow/whale/news/sector +
综合分）。待 hit_24h / realized_pnl 回填后，即可离线计算各因子与
命中率的 Spearman IC，驱动选币权重自适应（替代静态 AUTO_COIN_W_*）。

存储选型：与 0009/0013 同构 —— 单 JSONB 列（PG）/ TEXT 兜底（SQLite）。
字段可能随因子集演进，JSONB 改模型即可，不需要再写迁移。

多库安全 / 幂等
----------------
与 0009/0010/0013 一致：env.py 把 upgrade 依次跑在 core/market/analytics
三个逻辑库上，``auto_coin_selections`` 仅存在于 core 库，用
``inspector.has_table`` 守卫 no-op；``op.add_column`` 非幂等，用
``inspector.has_column`` 显式守卫跳过已存在列（全新库 create_all 已建 /
老库补齐 / 中途失败重跑均安全）。
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
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0009 / 0010 / 0013 同构)
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
    # auto_coin_selections 仅存在于 core 库；market/analytics bind 整体 no-op。
    if not _has_table("auto_coin_selections"):
        return

    if _has_column("auto_coin_selections", "factor_snapshot_json"):
        # 全新库 create_all 已建，或本迁移重跑 —— 跳过。
        return
    op.add_column(
        "auto_coin_selections",
        sa.Column("factor_snapshot_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op。列由 ORM 模型定义，真要回退应改模型 + 单独
    # 迁移，而非在这里盲目 drop 丢已写入的因子快照数据。
    # (与 0008 / 0009 / 0010 的保守 downgrade 语义一致。)
    pass
