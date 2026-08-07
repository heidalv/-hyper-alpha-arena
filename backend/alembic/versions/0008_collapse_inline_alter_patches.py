"""collapse inline on_startup ALTER patches

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-23

把 ``backend/main.py::on_startup`` 里残留的启动期内联 schema 补丁收口进 Alembic,
让 schema 由单一机制(Alembic)管理。

被收编的内联补丁
----------------
原 ``on_startup`` 里这 5 个块在每次启动都执行(虽各自幂等,但 schema 变更不应在应用
启动路径里发生):

  A. ``signal_trade_feedback.signal_type`` VARCHAR(30)→100  (core 库)
     —— 旧值 30 太短,AI 生成因子名(如 ``factor:cloud_microstructure_kyle``)超长,
        导致 bulk_save 整批回滚 → 开仓零快照 → IC 闭环收不到样本。
  B. ``ai_decision_logs`` 三列 prompt/reasoning/decision_snapshot TEXT  (analytics 库)
  C. ``ai_decision_logs`` 三周期 short/mid/long bias+confidence 六列  (analytics 库)
  D. ``global_sampling_configs.sampling_depth`` INTEGER NOT NULL DEFAULT 10  (core 库)
  E. ``crypto_klines`` exchange/environment 两列 + 关联索引  (market 库)

关系说明
--------
这些列**都已经在 ORM 模型中定义**(见 ``backend/database/models.py``),因此
0001 baseline 的 ``metadata.create_all()`` 在全新库上已能创建它们 —— 本迁移对全新库
基本是 no-op。这里要做的是:把对「已升级到 0001 但仍带历史 drift 的老库」的修正
固化进迁移,使应用启动路径不再触碰 schema。0008 应用后,``main.py`` 里对应的内联
块会被 gate 到仅在 < 0008 时执行(见 ``alembic_at_rev`` helper)。

多库安全
--------
和 0002 / 0006 / 0007 一致:env.py 把 upgrade 依次跑在 core/market/analytics 三个
逻辑库上。每张表只存在于一个库:
  - signal_trade_feedback / global_sampling_configs → core
  - ai_decision_logs                                 → analytics
  - crypto_klines                                    → market
用 ``inspector.has_table(...)`` 守卫:无该表的 bind 整体 no-op。

幂等
----
``op.add_column`` / ``op.alter_column`` 不是幂等的(重跑会报错),这里全部用
``inspector.has_column`` / 长度判断显式守卫,已存在的列跳过 —— 既支持全新库
(baseline 已建),也支持 drift 老库(补齐),也支持中途失败重跑。

SQLite 说明
-----------
 widening 走原生 ``ALTER TABLE ... ALTER COLUMN TYPE ...``(PostgreSQL 语法)。
SQLite 不支持该语句,但本仓库生产是 PostgreSQL;开发用 SQLite 时,baseline
``create_all`` 已按模型定义建出这些列,且 ``main.py`` 的内联 widening 块本身也是
PG-only(用 information_schema)。故迁移在 SQLite 下走 has_column 守卫分支,
不做 widening,保持安全 no-op。
"""
from __future__ import annotations

import os
import sys

from alembic import op
import sqlalchemy as sa

# 与 0001_baseline / env.py 保持一致:确保 backend 父目录在 sys.path 上。
_BACKEND_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_PARENT not in sys.path:
    sys.path.insert(0, _BACKEND_PARENT)

# revision identifiers, used by Alembic
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers
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


def _has_index(table_name: str, index_name: str) -> bool:
    insp = sa.inspect(_bind())
    try:
        indexes = insp.get_indexes(table_name)
    except Exception:
        return False
    return any(ix.get("name") == index_name for ix in indexes)


def _is_postgresql() -> bool:
    return _bind().dialect.name == "postgresql"


def _varchar_length(table_name: str, column_name: str):
    """PostgreSQL 下取 character_maximum_length;其他方言返回 None。"""
    if not _is_postgresql():
        return None
    res = _bind().execute(
        sa.text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table_name, "c": column_name},
    ).scalar()
    return res


# ─────────────────────────────────────────────────────────────────
# upgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # ── A. signal_trade_feedback.signal_type VARCHAR(30)→100 (core) ───────
    if _has_table("signal_trade_feedback") and _has_column("signal_trade_feedback", "signal_type"):
        _cur = _varchar_length("signal_trade_feedback", "signal_type")
        if _cur is not None and int(_cur) < 100:
            op.alter_column(
                "signal_trade_feedback",
                "signal_type",
                type_=sa.String(length=100),
                existing_type=sa.String(length=int(_cur)),
                existing_nullable=False,
            )

    # ── B. ai_decision_logs snapshot 三列 (analytics) ────────────────────
    if _has_table("ai_decision_logs"):
        for _col in ("prompt_snapshot", "reasoning_snapshot", "decision_snapshot"):
            if not _has_column("ai_decision_logs", _col):
                op.add_column(
                    "ai_decision_logs",
                    sa.Column(_col, sa.Text(), nullable=True),
                )

        # ── C. ai_decision_logs 三周期 short/mid/long bias+confidence ─────
        _tf_cols = [
            ("short_bias", sa.String(length=20)),
            ("short_confidence", sa.Float()),
            ("mid_bias", sa.String(length=20)),
            ("mid_confidence", sa.Float()),
            ("long_bias", sa.String(length=20)),
            ("long_confidence", sa.Float()),
        ]
        for _col, _typ in _tf_cols:
            if not _has_column("ai_decision_logs", _col):
                op.add_column(
                    "ai_decision_logs",
                    sa.Column(_col, _typ, nullable=True),
                )

    # ── D. global_sampling_configs.sampling_depth (core) ─────────────────
    if _has_table("global_sampling_configs"):
        if not _has_column("global_sampling_configs", "sampling_depth"):
            op.add_column(
                "global_sampling_configs",
                sa.Column(
                    "sampling_depth",
                    sa.Integer(),
                    server_default="10",
                    nullable=False,
                ),
            )

    # ── E. crypto_klines exchange/environment + 索引 (market) ────────────
    # 注:crypto_klines 表本身由 baseline create_all / 模型定义负责建立。
    # 本块只负责给老库补 exchange/environment 列及索引(全新库已具备)。
    if _has_table("crypto_klines"):
        if not _has_column("crypto_klines", "exchange"):
            op.add_column(
                "crypto_klines",
                sa.Column(
                    "exchange",
                    sa.String(length=20),
                    server_default="binance",
                    nullable=False,
                ),
            )
        if not _has_index("crypto_klines", "ix_crypto_klines_exchange"):
            op.create_index(
                "ix_crypto_klines_exchange", "crypto_klines", ["exchange"]
            )

        if not _has_column("crypto_klines", "environment"):
            op.add_column(
                "crypto_klines",
                sa.Column(
                    "environment",
                    sa.String(length=20),
                    server_default="mainnet",
                    nullable=False,
                ),
            )
        if not _has_index("crypto_klines", "ix_crypto_klines_environment"):
            op.create_index(
                "ix_crypto_klines_environment", "crypto_klines", ["environment"]
            )
        # 复合索引(应用启动补丁里也会创建它,这里同步收口)
        if not _has_index(
            "crypto_klines", "idx_crypto_klines_symbol_period_env"
        ):
            op.create_index(
                "idx_crypto_klines_symbol_period_env",
                "crypto_klines",
                ["symbol", "period", "environment"],
            )


# ─────────────────────────────────────────────────────────────────
# downgrade
# ─────────────────────────────────────────────────────────────────
def downgrade() -> None:
    # 收口迁移:downgrade 仅撤销 0008 新增物。对全新库(baseline 已含这些列)
    # 不应 drop —— 但 0008 的 add_column 仅在列不存在时执行,所以「0008 加了它」
    # 与「baseline 加了它」在 inspector 层面无法区分。为安全起见,downgrade 为 no-op:
    # 列由模型定义,真要回退应改模型 + 单独迁移,而非在这里盲目 drop 丢数据。
    # (与 0001 baseline 不可降级的保守语义一致。)
    pass
