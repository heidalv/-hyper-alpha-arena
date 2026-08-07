"""trading_wisdom add evaluation quality columns

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-05

v6 阶段2（S2-10）: wisdom 闭环增强 —— 为 ``trading_wisdom`` 加
``evaluation_count`` / ``quality_hit_count`` 两列，支撑"验证强度排序"：

- evaluation_count：通过质量闸门后的有效评估样本数（净扣费口径）；
- quality_hit_count：其中净盈利样本数。

配合 WisdomTracker 的净扣费 EMA 评分（按 |pnl| 金额加权信号），
build_wisdom_context 的验证强度排序 = effectiveness_score ×
min(1, quality_hit_count/MIN) × log(1+applied_count)，避免样本少但
碰巧高分的智慧霸榜。

存储选型：与 0009/0013 同构 —— PG 原生列 / SQLite 兜底。

多库安全 / 幂等
----------------
与 0009/0010/0013/0014 一致：env.py 把 upgrade 依次跑在 core/market/analytics
三个逻辑库上，``trading_wisdom`` 仅存在于 analytics 库（MarketBase），用
``inspector.has_table`` 守卫 no-op；``op.add_column`` 非幂等，用
``inspector.has_column`` 显式守卫跳过已存在列。
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
revision = "0015"
down_revision = "0014"
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


# 要加的 2 列：(列名, 类型)
_QUALITY_COLUMNS = [
    ("evaluation_count", sa.Integer()),
    ("quality_hit_count", sa.Integer()),
]


# ─────────────────────────────────────────────────────────────────
# upgrade / downgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # trading_wisdom 仅存在于 analytics 库；core/market bind 整体 no-op。
    if not _has_table("trading_wisdom"):
        return

    for col_name, col_type in _QUALITY_COLUMNS:
        if _has_column("trading_wisdom", col_name):
            # 全新库 create_all 已建，或本迁移重跑 —— 跳过。
            continue
        op.add_column(
            "trading_wisdom",
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op。列由 ORM 模型定义，真要回退应改模型 + 单独
    # 迁移，而非在这里盲目 drop 丢已写入的质量样本计数。
    # (与 0008 / 0009 / 0010 / 0014 的保守 downgrade 语义一致。)
    pass
