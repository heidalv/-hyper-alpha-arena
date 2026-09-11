"""tenant_id DEFAULT-1 泄漏回填：account 作用域表按 accounts.user_id 重新打标

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-03

背景（2026-09-03 全面修复批次发现）
------------------------------------
0004 给 account 作用域表（A1 组）加了 ``tenant_id INTEGER NOT NULL DEFAULT 1`` 并按
``accounts.user_id`` 回填；0005 对这些表 ENABLE + FORCE RLS。但其后 **17 个 ORM 模型一直
没有声明 tenant_id 列**（Position / Order / Trade / AccountAssetSnapshot / AccountStrategyConfig /
RiskControlConfig / SignalTradeFeedback / TradeMemoryRecord(早已补) / FullAutoSession / …），
``connection._auto_fill_tenant_id`` 只对"模型声明了列"的对象生效，于是这些模型的新行全部
落 DB DEFAULT 1。后果两层：

1. 属主是 326 / 327 的行被打成租户 1 → FORCE RLS 下属主与后台身份（AUTH_LOCAL_TENANT=326）
   都看不到：实测 risk_control_configs 14 行中 11 行、account_asset_snapshots 15 行中 13 行、
   signal_trade_feedback 3.4 万行、trade_memory_records 1562 行、paper_funding_ledger 449 行、
   ai_strategies 605 行处于"存在但对所有人不可见"状态（含实盘账户 188 的风控配置）。
2. 在请求上下文（GUC=326/327）内写这些表会被 WITH CHECK 拒：API 新建账户时的初始快照与
   默认风控配置创建一直失败（日志 InsufficientPrivilege）。

ORM 侧已在同批次补齐 17 个模型的 tenant_id 声明（新行不再泄漏）；本迁移负责历史行。

做什么
------
对每张同时具备 ``account_id`` 与 ``tenant_id`` 列的 A1 表：

    UPDATE <t> SET tenant_id = a.user_id FROM accounts a
    WHERE a.id = <t>.account_id AND <t>.tenant_id = 1 AND a.user_id <> 1 AND a.user_id IS NOT NULL

只动 ``tenant_id = 1 且属主 ≠ 1`` 的行——这正是 DEFAULT 泄漏的精确指纹（探针确认所有
错配都属此类，不存在"后台身份 326 写到 327 账户"的另一类错配）。属主本就是用户 1 的行、
以及 tenant_id 与属主一致的行都不碰。与 0004 的回填语义完全相同，只是补做了 0004 之后新增的行。

多库安全 / 幂等 / RLS
----------------------
- 仅 core 库（有 ``accounts`` 表）执行；market / analytics bind 整体 no-op。非 PostgreSQL 跳过。
- 在事务内 ``SET LOCAL app.is_admin = 'on'``：这些表 FORCE RLS，普通 owner 角色的 UPDATE 会被
  策略过滤成 0 行（见 0005 头注释）；superuser 本就绕过，SET 无副作用。
- 幂等：重跑时 WHERE 条件匹配 0 行。
- ``downgrade`` 为 no-op：旧值 1 是 DEFAULT 泄漏而非信息，恢复它没有意义且会重新隐藏数据。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

#: 0004 的 A1 组（account_id 单跳到 accounts.user_id）
A1_TABLES = (
    "positions", "orders", "trades", "account_asset_snapshots",
    "account_strategy_configs", "account_prompt_bindings", "ai_strategies",
    "hyperliquid_wallets", "hyperliquid_account_snapshots", "hyperliquid_positions",
    "hyperliquid_exchange_actions", "risk_control_configs", "paper_balances",
    "paper_positions", "paper_orders", "paper_funding_ledger", "position_exit_events",
    "trade_memory_records", "trader_mental_states", "trader_personalities",
    "signal_trade_feedback", "full_auto_sessions", "arbitrage_profiles",
)


def _bind():
    return op.get_bind()


def _is_pg() -> bool:
    try:
        return _bind().dialect.name == "postgresql"
    except Exception:
        return False


def _has_columns(table: str, *cols: str) -> bool:
    insp = sa.inspect(_bind())
    try:
        if not insp.has_table(table):
            return False
        names = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False
    return all(c in names for c in cols)


def upgrade() -> None:
    if not _is_pg() or not _has_columns("accounts", "id", "user_id"):
        return
    bind = _bind()
    # FORCE RLS 表上的 DML 需要管理员短路，否则非 superuser 角色会"看不到行"而静默更新 0 行
    bind.execute(sa.text("SET LOCAL app.is_admin = 'on'"))
    total = 0
    for t in A1_TABLES:
        if not _has_columns(t, "account_id", "tenant_id"):
            continue
        res = bind.execute(sa.text(
            f"UPDATE {t} AS x SET tenant_id = a.user_id "
            f"FROM accounts AS a "
            f"WHERE a.id = x.account_id AND x.tenant_id = 1 "
            f"AND a.user_id IS NOT NULL AND a.user_id <> 1"
        ))
        n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else 0
        total += n
        if n:
            print(f"[0020] {t}: restamped {n} row(s) tenant_id 1 -> accounts.user_id")
    print(f"[0020] total restamped rows: {total}")


def downgrade() -> None:
    # 有意 no-op：见模块头注释
    return
