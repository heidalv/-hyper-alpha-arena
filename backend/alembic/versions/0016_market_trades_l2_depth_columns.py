"""market_trades_aggregated add L2 depth columns

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-06

v6 阶段2（S2-1）: L2 重建层接线收口 —— 为 ``market_trades_aggregated`` 加
``bid_depth_top5`` / ``ask_depth_top5`` 两列（桶末帧前5档名义深度，px*sz，
USD 口径），由 BaseMarketFlowCollector._flush_trades 写入：

- L2Reconstructor（l2_reconstructor.py）已在采集管道 ingest 清洗后快照，
  维护逐 symbol 最新干净帧（跳变防护 + 深度派生）；
- flush 时从重建器取末帧计算前5档名义深度落库，无订单簿帧时为 NULL；
- 与 hyperliquid_collector._flush_orderbook 已落的 MarketOrderbookSnapshots
  （数量口径 bid_depth_5）互补：本列是名义深度，且与 KlineDepthAggregator
  输出列名（bid_depth_top5/ask_depth_top5）对齐，因子侧可直接消费。

存储选型：与 0013/0014/0015 同构 —— PG 原生列 / SQLite 兜底（DECIMAL→NUMERIC）。

多库安全 / 幂等
----------------
与 0015 一致：env.py 把 upgrade 依次跑在 core/market/analytics 三个逻辑库上，
``market_trades_aggregated`` 仅存在于 market 库（MarketBase），用
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
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# inspector helpers (与 0009 / 0010 / 0013 / 0015 同构)
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
_DEPTH_COLUMNS = [
    ("bid_depth_top5", sa.Numeric(24, 6)),
    ("ask_depth_top5", sa.Numeric(24, 6)),
]


# ─────────────────────────────────────────────────────────────────
# upgrade / downgrade
# ─────────────────────────────────────────────────────────────────
def upgrade() -> None:
    # market_trades_aggregated 仅存在于 market 库；core/analytics bind 整体 no-op。
    if not _has_table("market_trades_aggregated"):
        return

    for col_name, col_type in _DEPTH_COLUMNS:
        if _has_column("market_trades_aggregated", col_name):
            # 全新库 create_all 已建，或本迁移重跑 —— 跳过。
            continue
        op.add_column(
            "market_trades_aggregated",
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    # 收口迁移：downgrade 为 no-op。列由 ORM 模型定义，真要回退应改模型 + 单独
    # 迁移，而非在这里盲目 drop 丢已写入的深度样本。
    # (与 0008 / 0009 / 0010 / 0014 / 0015 的保守 downgrade 语义一致。)
    pass
