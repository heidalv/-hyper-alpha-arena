"""rls policies for multi-tenant isolation

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-23

Phase 3 Task 3.3: 为多租户 RLS 真正生效——在 3.1(加 tenant_id 列)与
3.2(每事务设 app.tenant_id / app.is_admin GUC)之上,为每张租户作用域表
ENABLE + FORCE RLS,并 CREATE POLICY。

两个变体
--------
- Variant 1(V1_TENANT_TABLES):3.1 加了 tenant_id 列的表。policy 用 tenant_id。
- Variant 2(V2_USERID_TABLES):Group B——没加 tenant_id,user_id 直接当租户键。
- ANALYTICS_V1 / ANALYTICS_V2:analytics 库上的两张表(同变体逻辑,不同列)。

策略语义(USING 读过滤 / WITH CHECK 写校验 同款)
------------------------------------------------
    <col> = current_setting('app.tenant_id', true)::int   -- 本租户
    OR <col> IS NULL                                       -- 全局行(所有租户可见)
    OR current_setting('app.is_admin', true) = 'on'        -- 阶段4 admin 短路

`current_setting(name, true)` 第二参数 true = 缺失时返回 NULL(不报错);NULL::int
是 NULL,故等号比较为假 → fail-closed(无 tenant 上下文时隐藏所有有属主的行)。
NULL tenant 行(全局)对所有租户可见——这是用户确认的"全局行共享"决策。

FORCE 的含义与陷阱(重要,给后续迁移作者)
-----------------------------------------
`ALTER TABLE ... FORCE ROW LEVEL SECURITY` 让**表 owner** 也受 RLS 约束。
但 PostgreSQL 的硬规则:**superuser 与 BYPASSRLS 角色永远绕过 RLS**,FORCE 不能
覆盖这一点。

后果:
  1. Alembic 的连接角色若为 superuser(如本环境的 db_admin),它自己的读写不会被
     RLS 过滤——所以**本迁移(创建策略)**不受影响:它跑在策略大量尚不存在时,
     且 superuser 本就绕过。
  2. **后续在已 FORCE 的表上做 ALTER/UPDATE/DELETE 的迁移**:若连接角色是普通
     owner(非 superuser),RLS 会过滤它的 DML!此时迁移可能"看不到行"或写入被
     WITH CHECK 拒。两种解决办法(任选其一):
       a) 迁移开头在 upgrade() 里:
            op.execute("SET LOCAL app.is_admin = 'on'")  # 需在事务内
            -- 或 SET app.is_admin = 'on'(session 级,跨事务)
       b) 用 superuser / BYPASSRLS 角色跑该迁移。
     这是为什么本迁移在每个 DDL 之间不需要特别处理,但 schema-migration 作者
     必须意识到:从 0005 起,对 FORCE 表的 DML 可能被 RLS 影响。

多 DB
-----
env.py 在 core/market/analytics 各跑一遍本迁移。绝大多数表只在 core 库;
has_table(...) 守卫让不存在的表静默跳过(例如 ATAS 表若未建)。仅 llm_usage_logs
在 analytics 库(V1),kline_ai_analysis_logs 在 analytics 库(V2)。

幂等
----
DROP POLICY IF EXISTS + CREATE,重复运行不报错。ENABLE/FORCE 也是幂等。
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


# ─────────────────────────────────────────────────────────────────
# Variant 1: 有 tenant_id 列的表(来自 0004 的 _ALL_TENANT_TABLES)
# ─────────────────────────────────────────────────────────────────
V1_TENANT_TABLES = [
    # A.1 account_id 单跳
    "positions", "orders", "trades", "account_asset_snapshots",
    "account_strategy_configs", "account_prompt_bindings", "ai_strategies",
    "hyperliquid_wallets", "hyperliquid_account_snapshots", "hyperliquid_positions",
    "hyperliquid_exchange_actions", "risk_control_configs", "paper_balances",
    "paper_positions", "paper_orders", "paper_funding_ledger", "position_exit_events",
    "trade_memory_records", "trader_mental_states", "trader_personalities",
    "signal_trade_feedback", "full_auto_sessions", "arbitrage_profiles",
    "binance_positions", "dingtalk_notifications",
    # A.2 会话消息表
    "ai_prompt_messages", "ai_signal_messages", "ai_attribution_messages",
    "alpha_assistant_messages",
    # A.3 策略链
    "strategy_memories", "strategy_trades", "prompt_training_records",
    "signal_performance_history",
    # A.4 长链
    "strategy_executions", "auto_coin_selections",
    "arbitrage_paper_exchange_balances", "arbitrage_paper_ledgers",
    "rebate_orders", "rebate_performance_logs", "rebate_trade_outcomes",
    # A.5 可空属主
    "atas_strategies", "scalp_signal_log", "rebate_positions",
    "arbitrage_paper_accounts", "exchange_credentials",
    # BYOK
    "llm_configurations",
]
# Variant 2: 复用 user_id 当租户键(Group B,0004 未加 tenant_id)
V2_USERID_TABLES = [
    "accounts", "user_auth_sessions", "refresh_tokens", "user_subscriptions",
    "user_exchange_config",
    "ai_prompt_conversations", "ai_signal_conversations",
    "ai_attribution_conversations", "alpha_assistant_conversations",
    "visual_strategies", "dashboard_layouts",
]
# AnalyticsBase 用户所有(分变体)
ANALYTICS_V1 = ["llm_usage_logs"]            # 0004 加了 tenant_id
ANALYTICS_V2 = ["kline_ai_analysis_logs"]    # AnalyticsBase,用 user_id


def _policy_sql(table: str, col: str) -> str:
    """生成 ENABLE + FORCE + DROP IF EXISTS + CREATE POLICY 的批量 SQL。

    USING(读)与 WITH CHECK(写)用同一谓词。参数:
      - table: 表名(已用 has_table 守卫,内联安全)
      - col:  'tenant_id' 或 'user_id'
    """
    return f"""
ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON "{table}";
CREATE POLICY tenant_isolation ON "{table}"
  USING (
    {col} = current_setting('app.tenant_id', true)::int
    OR {col} IS NULL
    OR current_setting('app.is_admin', true) = 'on'
  )
  WITH CHECK (
    {col} = current_setting('app.tenant_id', true)::int
    OR {col} IS NULL
    OR current_setting('app.is_admin', true) = 'on'
  );
"""


def _applied_ok(bind, table: str, col: str) -> bool:
    """确认该表的 policy 已正确建好(ENABLE+FORCE+policy 三者齐全)。

    用于幂等:迁移重跑时,已建好的表跳过,避免再次抢 AccessExclusiveLock。
    """
    try:
        row = bind.execute(
            sa.text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                "(SELECT count(*) FROM pg_policy p "
                " WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation') "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relname=:t AND n.nspname NOT IN ('pg_catalog','information_schema')"
            ),
            {"t": table},
        ).fetchone()
    except Exception:
        return False
    return bool(row and row[0] and row[1] and row[2] > 0)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 运行期放宽 lock_timeout:ALTER TABLE ENABLE/FORCE RLS 要 AccessExclusiveLock,
    # 与应用查询的 AccessShareLock 冲突。默认 15s 在繁忙库上常抢不到。这里放到
    # 60s,给短查询让路;配合下面的 SAVEPOINT 重试,个别表即使抢不到也只是 skip。
    try:
        bind.execute(sa.text("SET LOCAL lock_timeout = '60s'"))
    except Exception:
        pass

    def _do(table: str, col: str, variant: str) -> None:
        if not insp.has_table(table):
            return
        # 幂等:已建好则跳过(避免重跑重复抢锁)
        if _applied_ok(bind, table, col):
            print(f"[RLS] already ok {variant} {table}, skip")
            return
        # 每张表用独立 SAVEPOINT:一张表 ALTER 抢不到锁只回滚到 savepoint,
        # 不污染整个迁移事务(否则后续 has_table/op.execute 全部 InFailedSqlTransaction)。
        sp = bind.begin_nested()
        try:
            bind.execute(sa.text(_policy_sql(table, col)))
            sp.commit()
        except Exception as e:
            sp.rollback()
            print(f"[RLS] skip {variant} {table}: {e}")

    # Variant 1: tenant_id 列
    for t in V1_TENANT_TABLES + ANALYTICS_V1:
        _do(t, "tenant_id", "V1")

    # Variant 2: user_id 列
    for t in V2_USERID_TABLES + ANALYTICS_V2:
        _do(t, "user_id", "V2")


def downgrade() -> None:
    """best-effort:删策略 + 关 FORCE + 关 RLS。每表独立 SAVEPOINT。"""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        bind.execute(sa.text("SET LOCAL lock_timeout = '60s'"))
    except Exception:
        pass
    all_t = V1_TENANT_TABLES + V2_USERID_TABLES + ANALYTICS_V1 + ANALYTICS_V2
    for t in all_t:
        if not insp.has_table(t):
            continue
        sp = bind.begin_nested()
        try:
            bind.execute(sa.text(f'DROP POLICY IF EXISTS tenant_isolation ON "{t}";'))
            bind.execute(sa.text(f'ALTER TABLE "{t}" NO FORCE ROW LEVEL SECURITY;'))
            bind.execute(sa.text(f'ALTER TABLE "{t}" DISABLE ROW LEVEL SECURITY;'))
            sp.commit()
        except Exception as e:
            sp.rollback()
            print(f"[RLS] downgrade skip {t}: {e}")
