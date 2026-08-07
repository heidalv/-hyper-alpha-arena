"""Alembic environment configuration for Hyper-Alpha-Arena.

多 base 支持：本系统拆分为三个独立数据库（见 backend/database/connection.py）：
  - Core      (Base)            — engine            — 交易核心 (alpha_arena)
  - Market    (MarketBase)      — market_engine     — 市场数据 (alpha_market)
  - Analytics (AnalyticsBase)   — analytics_engine  — 分析审计 (alpha_analytics)

每个 base 使用独立的 version_table（alembic_version_core/market/analytics），
因此三个库各自跟踪版本历史，互不干扰。

注意：
  - 生产环境是 3 个独立 PostgreSQL 库；开发环境通常是 3 个 SQLite 文件。
    engine / market_engine / analytics_engine 已在 connection.py 模块级按各自
    DATABASE_URL / MARKET_DATABASE_URL / ANALYTICS_DATABASE_URL 创建好，这里直接复用。
  - import 本模块会触发 connection.py 的连接池/监控线程初始化。若数据库未启动，
    import 仍可成功（create_engine 是惰性的，不立即建连），但 alembic 在线命令
    执行时才会真正尝试连接。
  - 离线模式（alembic upgrade head --sql）按每个 target 各自的 engine.url 生成
    SQL，可正确区分三个库的方言；不会真正连库。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context

# 确保 backend 包可被 import（alembic 通过 script_location 加载本文件，
# sys.path 未必包含 backend 父目录）。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

# 加载项目根 .env，确保 connection.py 读到正确的 DATABASE_URL 等。
try:
    from dotenv import load_dotenv as _load_dotenv

    _ENV_CANDIDATES = (
        os.path.join(_BACKEND_PARENT, ".env"),            # Hyper-Alpha-Arena/.env
        os.path.join(_BACKEND_PARENT, "backend", ".env"),  # 兼容
    )
    for _env_path in _ENV_CANDIDATES:
        if os.path.isfile(_env_path):
            _load_dotenv(_env_path, override=False)
            break
except ImportError:
    pass

# 三个 declarative base + 各自 engine。
# 注意：Base/MarketBase/AnalyticsBase 必须从 models 导入——只有 import models
# 时才会执行各表类的定义、把表注册进对应 metadata。若直接从 connection 导入 base，
# metadata 是空的（connection 不 import models），autogenerate 将生成不了任何表。
# engine / market_engine / analytics_engine 则来自 connection（模块级按各自 URL 创建）。
from backend.database.models import Base, MarketBase, AnalyticsBase  # noqa: E402
from backend.database.connection import (  # noqa: E402
    engine,
    market_engine,
    analytics_engine,
)

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ─────────────────────────────────────────────────────────────────
# 三套 (metadata, engine, label, version_table)
# version_table 各自独立 → 三个库的版本历史互不干扰。
# ─────────────────────────────────────────────────────────────────
TARGETS = [
    (Base.metadata, engine, "core", "alembic_version_core"),
    (MarketBase.metadata, market_engine, "market", "alembic_version_market"),
    (AnalyticsBase.metadata, analytics_engine, "analytics", "alembic_version_analytics"),
]


def run_migrations_offline() -> None:
    """离线模式：对每个 base 用其 engine.url 各自生成 SQL，不真正连库。

    每个 target 使用自己的 version_table，生成的 SQL 会落到对应的版本表。
    """
    for metadata, eng, _label, version_table in TARGETS:
        context.configure(
            url=str(eng.url),
            target_metadata=metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
            version_table=version_table,
        )
        with context.begin_transaction():
            context.run_migrations()


def _run_for_target(metadata, eng, version_table) -> None:
    """对单个 base/engine 跑在线迁移。"""
    with eng.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            compare_type=True,
            version_table=version_table,
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：依次对三个 base 各跑一次，各自独立 version_table。"""
    for metadata, eng, _label, version_table in TARGETS:
        _run_for_target(metadata, eng, version_table)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
