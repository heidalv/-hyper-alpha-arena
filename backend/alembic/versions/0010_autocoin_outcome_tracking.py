"""autocoin add outcome tracking columns

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23

阶段A Task: AutoCoin 反馈闭环 —— 给 ``auto_coin_selections`` 表加 6 个表现回填列。

背景
----
``auto_coin_selections`` 表原本只记录"何时注入/淘汰"一个币种，没有任何
*事后表现*字段。无法计算命中率("命中率")：既没有选中时刻价格
(price_at_selection)，也没有 24h/72h 后价格，没有命中标志，也没有已实现
盈亏。本迁移补齐这 6 列，使阶段A 能在注入时记录 price_at_selection，并由
后续回填作业填 price_after_24h/72h、hit_24h/72h、realized_pnl。

列清单
------
- price_at_selection  Numeric(20,8)  注入时刻价格(injected 写入)
- price_after_24h     Numeric(20,8)  24h 后回填
- price_after_72h     Numeric(20,8)  72h 后回填
- realized_pnl        Numeric(16,4)  平仓后回填
- hit_24h             Boolean          24h 后是否"命中"(价格上涨)
- hit_72h             Boolean          72h 后是否"命中"

多库安全
--------
和 0009 同构：env.py 把 upgrade 依次跑在 core/market/analytics 三个逻辑库
上。``auto_coin_selections`` 表只存在于 core 库。用
``inspector.has_table(...)`` 守卫：无该表的 bind 整体 no-op。

幂等
----
``op.add_column`` 不是幂等的(重跑会报错)，这里用 ``inspector.has_column``
显式守卫，列已存在则跳过 —— 既支持全新库(baseline create_all 已按模型建出
这些列)，也支持从 0009 升级的老库(补齐)，也支持中途失败重跑。

数值类型
--------
SQLAlchemy 的 Numeric 在 PG 上映射为 NUMERIC，SQLite/其它方言兜底为
REAL/BLOB 也够用(本表精度要求不高，只是给反馈统计用)。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0008 / 0009 同构)
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


# 要加的 6 列：(列名, 类型)
_OUTCOME_COLUMNS = [
    ("price_at_selection", sa.Numeric(20, 8)),
    ("price_after_24h", sa.Numeric(20, 8)),
    ("price_after_72h", sa.Numeric(20, 8)),
    ("realized_pnl", sa.Numeric(16, 4)),
    ("hit_24h", sa.Boolean()),
    ("hit_72h", sa.Boolean()),
]


# ─────────────────────────────────────────────────────────────────
# upgrade / downgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # auto_coin_selections 仅存在于 core 库；market/analytics bind 整体 no-op。
    if not _has_table("auto_coin_selections"):
        return

    for col_name, col_type in _OUTCOME_COLUMNS:
        if _has_column("auto_coin_selections", col_name):
            # 全新库 create_all 已建，或本迁移重跑 —— 跳过。
            continue
        op.add_column(
            "auto_coin_selections",
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op。列由 ORM 模型定义，真要回退应改模型 + 单独
    # 迁移，而非在这里盲目 drop 丢已写入的反馈数据。
    # (与 0008 / 0009 的保守 downgrade 语义一致。)
    pass
