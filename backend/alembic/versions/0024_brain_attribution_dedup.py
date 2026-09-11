"""brain_attribution 去重 + 唯一索引（幂等写穿）

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-09

## 背景（2026-09-09 根因调查）

`backend/services/source_attribution.py::_record_attribution_row` 的 docstring
自称「幂等表」，但 INSERT 语句**没有 ON CONFLICT**，表上也只有 `id` 主键——
每次平仓事件都会新增一行。实测（alpha_arena）：

    total=1323 行,  distinct(position_id, src, tier)=721  →  602 行重复
    单仓最多重复 18 行（position_id=2, src=unknown, tier=short）

后果：任何按 `brain_attribution` 做的来源归因（src 信用、通道熔断、
`tier=mid src=llm` 这类统计）都会**按重复次数加权**，结论失真。
本次调查就曾因此得到「LLM mid 全亏 -238」的错误结论（238 行
`pnl` 恒 -1.0 的占位行集中在 2026-08-29 14:59–18:34）。

## 本迁移

1. 按 `(position_id, src, tier)` 去重，保留 **id 最大**（最新写入）的一行；
2. 建唯一索引 `uq_brain_attribution_pos_src_tier`；
3. 之后写入端改用 `ON CONFLICT ... DO UPDATE`（同批改动）。

幂等：`_has_index` 守卫；重复执行无副作用。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_brain_attribution_pos_src_tier"


def _bind():
    return op.get_bind()


def _has_table(table_name: str) -> bool:
    try:
        return sa.inspect(_bind()).has_table(table_name)
    except Exception:
        return False


def _has_index(table_name: str, index_name: str) -> bool:
    try:
        insp = sa.inspect(_bind())
        return any(ix.get("name") == index_name for ix in insp.get_indexes(table_name))
    except Exception:
        return False


def upgrade() -> None:
    if not _has_table("brain_attribution"):
        return
    bind = _bind()

    # 1) 去重：同 (position_id, src, tier) 只保留最新一行
    bind.execute(sa.text("""
        DELETE FROM brain_attribution b
        USING brain_attribution k
        WHERE b.position_id = k.position_id
          AND b.src = k.src
          AND b.tier = k.tier
          AND b.id < k.id
    """))

    # 2) 唯一索引（幂等）
    if not _has_index("brain_attribution", _INDEX_NAME):
        bind.execute(sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
            f"ON brain_attribution (position_id, src, tier)"
        ))


def downgrade() -> None:
    if _has_table("brain_attribution") and _has_index("brain_attribution", _INDEX_NAME):
        _bind().execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
