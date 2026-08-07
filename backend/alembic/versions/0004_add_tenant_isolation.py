"""add tenant_id for multi-tenant isolation

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23

Phase 3 Task 3.1: 为多租户 RLS 奠基。
- Group A: 给 ~35 个租户作用域表加 tenant_id，并按 FK 链回填归属。
- Group B (user_id 直接复用) 与 Group C (全局) 本轮不动。
- 分类已与用户确认（见任务说明 4 项决策）。

回填链分组：
  A.1  account_id 单跳 → accounts.user_id
  A.2  conversation_id → *_conversations.user_id
  A.3  strategy_id(String) → ai_strategies.strategy_id → account_id → user_id
  A.4  长链：strategy_executions / auto_coin_selections / arbitrage_paper_* / rebate_*
  A.5  可空属主：tenant_id 保持 NULLABLE，NULL = 全局可见

多 DB 安全：env.py 会在 core/market/analytics 三个库各跑一遍本迁移；绝大多数
表只在 core 库，用 inspector.has_table(...) 守卫让 market/analytics 跳过。
仅 llm_usage_logs 在 analytics 库。
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# 表清单（与用户确认的分类一一对应）
# ─────────────────────────────────────────────────────────────────

# A.1 单跳：account_id NOT NULL → accounts.user_id（回填后 NOT NULL）
A1_TABLES = [
    "positions", "orders", "trades", "account_asset_snapshots",
    "account_strategy_configs", "account_prompt_bindings", "ai_strategies",
    "hyperliquid_wallets", "hyperliquid_account_snapshots", "hyperliquid_positions",
    "hyperliquid_exchange_actions", "risk_control_configs", "paper_balances",
    "paper_positions", "paper_orders", "paper_funding_ledger", "position_exit_events",
    "trade_memory_records", "trader_mental_states", "trader_personalities",
    "signal_trade_feedback", "full_auto_sessions", "arbitrage_profiles",
    "binance_positions",
]
# A.1 可空 account_id（tenant_id 保持 NULLABLE，NULL = 全局可见）
A1_NULLABLE = ["dingtalk_notifications"]

# A.2 会话链：(消息表, 会话表) — conversation_id → *_conversations.id → user_id
A2_CONV = [
    ("ai_prompt_messages", "ai_prompt_conversations"),
    ("ai_signal_messages", "ai_signal_conversations"),
    ("ai_attribution_messages", "ai_attribution_conversations"),
    ("alpha_assistant_messages", "alpha_assistant_conversations"),
]

# A.3 策略链：strategy_id(String) → ai_strategies.strategy_id → account_id → user_id
A3_STRAT = ["strategy_memories", "strategy_trades", "prompt_training_records", "signal_performance_history"]

# A.4 长链：每张表独立回填 SQL（见 upgrade() 内）
# A.5 可空属主（tenant_id NULLABLE）
A5_NULLABLE = ["atas_strategies", "scalp_signal_log", "rebate_positions",
               "arbitrage_paper_accounts", "exchange_credentials"]

# upgrade 中加了 tenant_id 的全部表（供 downgrade 用）
_ALL_TENANT_TABLES = [
    *A1_TABLES, *A1_NULLABLE,
    *[t for t, _ in A2_CONV], *A3_STRAT,
    "strategy_executions", "auto_coin_selections",
    "arbitrage_paper_exchange_balances", "arbitrage_paper_ledgers",
    "rebate_orders", "rebate_performance_logs", "rebate_trade_outcomes",
    *A5_NULLABLE,
    "llm_configurations", "llm_usage_logs",
]


def _add_tenant_column(tablename: str, inspector) -> None:
    """幂等加列：tenant_id INTEGER NULLABLE + 索引。
    已存在则跳过（防止部分回滚后重跑报错）。类型用 Integer 与 users.id 对齐。"""
    cols = {c["name"] for c in inspector.get_columns(tablename)}
    if "tenant_id" in cols:
        return
    op.add_column(tablename, sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.create_index(f"ix_{tablename}_tenant_id", tablename, ["tenant_id"])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has(t: str) -> bool:
        return inspector.has_table(t)

    # default 用户子查询（Task 2.1 已确认存在 default admin）。
    default_user_subq = "(SELECT id FROM users WHERE username='default' LIMIT 1)"

    # ─── A.1 account_id 单跳 ───
    for t in A1_TABLES + A1_NULLABLE:
        if not _has(t):
            continue
        nullable = t in A1_NULLABLE
        _add_tenant_column(t, inspector)
        op.execute(
            f'UPDATE "{t}" SET tenant_id = a.user_id '
            f'FROM accounts a '
            f'WHERE "{t}".account_id = a.id AND "{t}".tenant_id IS NULL'
        )
        if not nullable:
            # account_id NOT NULL 表回填后理论上无 NULL；兜底归 default。
            op.execute(f'UPDATE "{t}" SET tenant_id = {default_user_subq} WHERE tenant_id IS NULL')
            op.alter_column(t, "tenant_id", nullable=False)

    # ─── A.2 会话链 ───
    for tbl, conv in A2_CONV:
        if not _has(tbl) or not _has(conv):
            continue
        _add_tenant_column(tbl, inspector)
        op.execute(
            f'UPDATE "{tbl}" SET tenant_id = c.user_id '
            f'FROM "{conv}" c '
            f'WHERE "{tbl}".conversation_id = c.id AND "{tbl}".tenant_id IS NULL'
        )
        op.execute(f'UPDATE "{tbl}" SET tenant_id = {default_user_subq} WHERE tenant_id IS NULL')
        op.alter_column(tbl, "tenant_id", nullable=False)

    # ─── A.3 策略链：strategy_id(String) → ai_strategies ───
    for t in A3_STRAT:
        if not _has(t) or not _has("ai_strategies"):
            continue
        _add_tenant_column(t, inspector)
        op.execute(
            f'''UPDATE "{t}" SET tenant_id = subq.user_id FROM (
                SELECT s.strategy_id AS sid, a.user_id AS user_id
                FROM ai_strategies s JOIN accounts a ON s.account_id = a.id
            ) subq WHERE "{t}".strategy_id = subq.sid AND "{t}".tenant_id IS NULL'''
        )
        op.execute(f'UPDATE "{t}" SET tenant_id = {default_user_subq} WHERE tenant_id IS NULL')
        op.alter_column(t, "tenant_id", nullable=False)

    # ─── A.4 长链（每张表独立 SQL） ───
    # strategy_executions: strategy_id(Integer) → visual_strategies.id → user_id
    if _has("strategy_executions") and _has("visual_strategies"):
        _add_tenant_column("strategy_executions", inspector)
        op.execute(
            '''UPDATE "strategy_executions" SET tenant_id = v.user_id
               FROM visual_strategies v
               WHERE "strategy_executions".strategy_id = v.id
                 AND "strategy_executions".tenant_id IS NULL'''
        )
        op.execute(f'UPDATE "strategy_executions" SET tenant_id = {default_user_subq} WHERE tenant_id IS NULL')
        op.alter_column("strategy_executions", "tenant_id", nullable=False)

    # auto_coin_selections: session_id → full_auto_sessions.session_id → account_id → user_id
    if _has("auto_coin_selections") and _has("full_auto_sessions"):
        _add_tenant_column("auto_coin_selections", inspector)
        op.execute(
            '''UPDATE "auto_coin_selections" SET tenant_id = subq.user_id FROM (
                   SELECT f.session_id AS sid, a.user_id AS user_id
                   FROM full_auto_sessions f JOIN accounts a ON f.account_id = a.id
               ) subq
               WHERE "auto_coin_selections".session_id = subq.sid
                 AND "auto_coin_selections".tenant_id IS NULL'''
        )
        op.execute(f'UPDATE "auto_coin_selections" SET tenant_id = {default_user_subq} WHERE tenant_id IS NULL')
        op.alter_column("auto_coin_selections", "tenant_id", nullable=False)

    # arbitrage_paper_*: account_id → arbitrage_paper_accounts.id → owner_account_id → user_id
    # （tenant_id 保持 NULLABLE：owner 为空的行留 NULL = 全局可见）
    for t in ["arbitrage_paper_exchange_balances", "arbitrage_paper_ledgers"]:
        if not _has(t) or not _has("arbitrage_paper_accounts"):
            continue
        _add_tenant_column(t, inspector)
        op.execute(
            f'''UPDATE "{t}" SET tenant_id = subq.user_id FROM (
                   SELECT ap.id AS aid, a.user_id AS user_id
                   FROM arbitrage_paper_accounts ap
                   JOIN accounts a ON ap.owner_account_id = a.id
               ) subq
               WHERE "{t}".account_id = subq.aid AND "{t}".tenant_id IS NULL'''
        )

    # rebate_*: position_id → rebate_positions.position_id → owner_account_id → user_id
    # （tenant_id NULLABLE：当前数据 owner_account_id 全 NULL → 保持 NULL）
    for t in ["rebate_orders", "rebate_performance_logs", "rebate_trade_outcomes"]:
        if not _has(t) or not _has("rebate_positions"):
            continue
        _add_tenant_column(t, inspector)
        op.execute(
            f'''UPDATE "{t}" SET tenant_id = subq.user_id FROM (
                   SELECT rp.position_id AS pid, a.user_id AS user_id
                   FROM rebate_positions rp
                   JOIN accounts a ON rp.owner_account_id = a.id
               ) subq
               WHERE "{t}".position_id = subq.pid AND "{t}".tenant_id IS NULL'''
        )

    # ─── A.5 可空属主（tenant_id NULLABLE，按属主回填，无属主留 NULL） ───
    # atas_strategies: user_id 复制到 tenant_id
    if _has("atas_strategies"):
        _add_tenant_column("atas_strategies", inspector)
        op.execute('UPDATE "atas_strategies" SET tenant_id = user_id WHERE user_id IS NOT NULL')

    # 其余 A5：先建列+索引
    for t in ["scalp_signal_log", "rebate_positions", "arbitrage_paper_accounts", "exchange_credentials"]:
        if not _has(t):
            continue
        _add_tenant_column(t, inspector)

    # 按各自可空属主列回填
    if _has("scalp_signal_log"):
        op.execute(
            'UPDATE "scalp_signal_log" SET tenant_id = a.user_id '
            'FROM accounts a '
            'WHERE "scalp_signal_log".account_id = a.id AND "scalp_signal_log".tenant_id IS NULL'
        )
    if _has("rebate_positions"):
        op.execute(
            'UPDATE "rebate_positions" SET tenant_id = a.user_id '
            'FROM accounts a '
            'WHERE "rebate_positions".owner_account_id = a.id AND "rebate_positions".tenant_id IS NULL'
        )
    if _has("arbitrage_paper_accounts"):
        op.execute(
            'UPDATE "arbitrage_paper_accounts" SET tenant_id = a.user_id '
            'FROM accounts a '
            'WHERE "arbitrage_paper_accounts".owner_account_id = a.id '
            'AND "arbitrage_paper_accounts".tenant_id IS NULL'
        )
    if _has("exchange_credentials"):
        # user_id 复制到 tenant_id（user_id 可空）
        op.execute('UPDATE "exchange_credentials" SET tenant_id = user_id WHERE user_id IS NOT NULL')

    # ─── llm_configurations (BYOK §6.4) ───
    # 现有全局配置归 default admin；新建行由应用层填 tenant。
    if _has("llm_configurations"):
        _add_tenant_column("llm_configurations", inspector)
        op.execute(f'UPDATE "llm_configurations" SET tenant_id = {default_user_subq} WHERE tenant_id IS NULL')
        op.alter_column("llm_configurations", "tenant_id", nullable=False)

    # ─── llm_usage_logs (AnalyticsBase) ───
    # 只在 analytics 库存在（其它库被 _has 守卫跳过）。
    # 注意跨库约束：llm_usage_logs 在 analytics 库，但 accounts 表在 core 库，
    # 同一连接里无法 JOIN accounts。因此仅当本库存在 accounts 时回填
    # （单库 dev 环境 core=analytics 时成立；生产 3 库分离时 analytics 无 accounts
    # → tenant_id 留 NULL，由应用层/跨库回填任务补，NULL = 全局可见）。
    if _has("llm_usage_logs"):
        _add_tenant_column("llm_usage_logs", inspector)
        if _has("accounts"):
            op.execute(
                'UPDATE "llm_usage_logs" SET tenant_id = a.user_id '
                'FROM accounts a '
                'WHERE "llm_usage_logs".account_id = a.id AND "llm_usage_logs".tenant_id IS NULL'
            )
        # 保持 NULLABLE


def downgrade() -> None:
    """best-effort：删 tenant_id 列。RLS 策略由后续 0005（如存在）管理。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for t in _ALL_TENANT_TABLES:
        if not inspector.has_table(t):
            continue
        try:
            op.drop_index(f"ix_{t}_tenant_id", table_name=t)
        except Exception:
            pass
        try:
            op.drop_column(t, "tenant_id")
        except Exception:
            pass
