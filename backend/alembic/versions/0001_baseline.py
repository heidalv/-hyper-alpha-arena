"""baseline schema

Revision ID: 0001
Revises:
Create Date: 2026-07-23

冻结当前数据库 schema 作为初始 baseline。

实现策略：Strategy B（create_all）
---------------------------------
未采用 ``alembic revision --autogenerate``（Strategy A）。原因：经比对，线上
PostgreSQL 与模型 metadata 之间存在 drift —— DB 中存在 10 张模型里已不存在的
孤儿表（见下方“已知 drift”）。autogenerate 会生成 ``op.drop_table`` 去删除这些
表，对 baseline 来说是破坏性行为（baseline 只应 CREATE，不应 DESTROY）。
逐表清洗一份 ~139 表的 autogenerate 输出既繁琐又易错。

因此采用与应用启动时完全一致的 ``metadata.create_all()`` 方式：upgrade 时对三个
base 的 metadata 调用 ``create_all(engine, checkfirst=True)``。``checkfirst=True``
（默认）保证已存在的表不会被重建，所以对已有数据的线上库执行 upgrade 是幂等且
安全的（near no-op，仅写入 alembic 版本表）。

这一 baseline 的语义是「按当前模型定义建立全部表」，与 ``main.py`` 启动时的
``create_all`` 行为一致，可信赖地在新库上重建出可工作的 schema。后续迁移（阶段2
auth 列、阶段3 tenant_id）在此 baseline 之上做增量 ALTER。

已知 drift（DB 有、模型无的孤儿表，baseline 不会处理它们）
---------------------------------------------------------
Core (4):      ai_analysis_logs, ai_decision_logs, decision_snapshots, risk_control_events
Market (1):    raw_market_events
Analytics (5): mlto_debate_log, mlto_memory_events, mlto_signal_weights,
               mlto_thesis, mlto_thesis_events

这些表存在于线上 DB 但已从 ORM 模型中移除。baseline 只按模型建表，因此重建出的
新库不会包含它们。如需保留，应在后续显式迁移中重建。

表数量（模型定义）
------------------
Core: 104, Market: 12, Analytics: 23 —— 合计 139 表。
"""
from __future__ import annotations

import os
import sys

from alembic import op

# 与 env.py 一致：确保 backend 父目录在 sys.path 上，以便 import backend.*。
# 注意：alembic heads/history 这类命令不会执行 env.py（只有 upgrade/downgrade 才会），
# 因此版本文件自身必须保证 sys.path 就绪，否则 ModuleNotFoundError: No module named 'backend'。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

# 注意：必须从 backend.database.models 导入 base —— 只有 import models 时各表类
# 才会被定义并注册进对应 metadata；直接从 connection 导入 base 会得到空 metadata。
# engine / market_engine / analytics_engine 来自 connection，按各自环境变量创建。
from backend.database.models import Base, MarketBase, AnalyticsBase  # noqa: E402
from backend.database.connection import (  # noqa: E402
    engine,
    market_engine,
    analytics_engine,
)

# revision identifiers, used by Alembic
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Strategy B：对三个 base 的 metadata 分别 create_all。
    # checkfirst=True（默认）→ 已存在的表跳过，对线上库幂等安全。
    # 三个库各自独立，互不影响。
    Base.metadata.create_all(engine)
    MarketBase.metadata.create_all(market_engine)
    AnalyticsBase.metadata.create_all(analytics_engine)


def downgrade() -> None:
    # baseline 不可降级：会丢失全部业务数据。
    raise NotImplementedError("baseline 不可降级（将丢失全部数据）")
