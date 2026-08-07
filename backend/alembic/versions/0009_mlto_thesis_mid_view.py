"""mlto_thesis add mid_view column

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-23

阶段2 Task: thesis 模型扩展 —— 为长线 thesis 加 ``mid_view`` 子结构存储。

背景
----
长线（long tier）thesis 需要嵌入一份中周期(1h/4h)择时分析作为子视图
（MidViewDTO：direction/timing_score/timing_rationale/key_levels/
invalidation_for_timing/updated_at）。中周期只做择时、不做方向；方向仍由
长线 thesis 本体决定。mid_view=None 时退化为现状（向后兼容）。

存储选型：单 JSONB 列
----------------------
不用 5 个标量列（mid_direction / mid_timing_score / ...），原因：
  - 子结构字段可能演进（阶段3 decision_hub 可能再扩字段），JSONB 改模型即可，
    不需要再写迁移。
  - 读取/写入都是整体序列化（thesis_store._persist / _row_to_dto）。
PG 用 JSONB（可索引/可查询），SQLite/其它方言兜底 TEXT（本表仅 analytics 库有，
生产为 PostgreSQL；开发 SQLite 用 TEXT 也能存 JSON 字符串）。

多库安全
--------
和 0002 / 0006 / 0007 / 0008 一致：env.py 把 upgrade 依次跑在 core/market/
analytics 三个逻辑库上。``mlto_thesis`` 表只存在于 analytics 库。用
``inspector.has_table(...)`` 守卫：无该表的 bind 整体 no-op。

幂等
----
``op.add_column`` 不是幂等的（重跑会报错），这里用 ``inspector.has_column``
显式守卫，列已存在则跳过 —— 既支持全新库（baseline create_all 已按模型建出
此列），也支持从 0008 升级的老库（补齐），也支持中途失败重跑。

SQLite 说明
-----------
SQLite 没有 JSONB 类型。这里对方言分支：postgresql 用 JSONB，其它（含
SQLite）用 TEXT。开发环境 SQLite 下 create_all 已按 ORM（Text）建出列，
迁移走 has_column 守卫分支为 no-op。
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
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0008 同构)
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

    if _has_column("mlto_thesis", "mid_view_json"):
        # 全新库 create_all 已建，或本迁移重跑 —— 跳过。
        return

    # PG 用 JSONB（可查询/可索引），其它方言（含 SQLite）兜底 TEXT。
    if _is_postgresql():
        col_type = sa.dialects.postgresql.JSONB(astext_type=sa.Text())
    else:
        col_type = sa.Text()

    op.add_column(
        "mlto_thesis",
        sa.Column("mid_view_json", col_type, nullable=True),
    )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op。列由 ORM 模型定义，真要回退应改模型 + 单独
    # 迁移，而非在这里盲目 drop 丢已写入的 mid_view 数据。
    # (与 0008 的保守 downgrade 语义一致。)
    pass
