"""live_sub_positions table

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-23

Phase 2 Task: 实盘子仓位账本 —— 新建 ``live_sub_positions`` 表。

背景
----
HL One-Way mode 下交易所端 per-symbol per-account 只有一个净仓位 + 一个杠杆
档位,但本地策略按 trade_nature(scalp / trend_follow)分仓独立决策。本表是
live 侧的子仓位账本:本地按 tier 拆分跟踪,交易所只见聚合净仓位。
``LivePositionManager`` 在下单时计算差额(净变化)只发一笔给交易所,并在此记账。

多库安全
--------
和 0009 / 0010 同构:env.py 把 upgrade 依次跑在 core/market/analytics 三个
逻辑库上。``live_sub_positions`` 仅存在于 core 库(其 account_id 外键引用
``accounts.id``,而 accounts 表只在 core 库)。用
``inspector.has_table("accounts")`` 守卫:无 accounts 表的 bind(market/
analytics)整体 no-op。

实现策略:Strategy B(create_all)
---------------------------------
不采用 autogenerate 的 ``op.create_table`` 逐列罗列。原因:本表 15 列、含
server_default/onupdate/外键,逐列写 op.create_table 易与模型漂移。直接用
``Base.metadata.create_all(engine, tables=[LiveSubPosition.__table__])`` 与
应用启动时的建表行为完全一致,且 ``checkfirst=True``(默认)保证已存在则跳过,
对线上库幂等安全(与 0001 baseline 同策略)。

幂等
----
``create_all(checkfirst=True)`` 本身幂等:表已存在则 no-op。既支持全新库
(create_all 建表),也支持从 0010 升级的老库(补建),也支持中途失败重跑。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

# 与 0001_baseline / env.py 保持一致:确保 backend 父目录在 sys.path 上。
# 注意:alembic heads/history 这类命令不会执行 env.py(只有 upgrade/downgrade 才会),
# 因此版本文件自身必须保证 sys.path 就绪,否则 ModuleNotFoundError: No module named 'backend'。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)


# revision identifiers, used by Alembic
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0008 / 0009 / 0010 同构)
# ─────────────────────────────────────────────────────────────────
def _bind():
    return op.get_bind()


def _has_table(table_name: str) -> bool:
    insp = sa.inspect(_bind())
    try:
        return insp.has_table(table_name)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# upgrade / downgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # live_sub_positions 仅存在于 core 库(外键引用 accounts.id)。
    # market/analytics bind 无 accounts 表 → 整体 no-op。
    if not _has_table("accounts"):
        return

    # 已存在则跳过(checkfirst=True 默认)—— 幂等。
    if _has_table("live_sub_positions"):
        return

    # Strategy B:用 ORM metadata 建表,与应用启动时 create_all 行为一致。
    # 此处 import 触发模型注册(确保 LiveSubPosition.__table__ 在 metadata 中)。
    from backend.database.models import Base, LiveSubPosition
    from backend.database.connection import engine

    Base.metadata.create_all(
        engine,
        tables=[LiveSubPosition.__table__],
        checkfirst=True,
    )


def downgrade() -> None:
    # 收口迁移:downgrade 为 no-op。表数据由 LivePositionManager 写入,真要回退
    # 应改模型 + 单独迁移,而非在这里盲目 drop 丢已写入的子仓位账本数据。
    # (与 0008 / 0009 / 0010 的保守 downgrade 语义一致。)
    pass
