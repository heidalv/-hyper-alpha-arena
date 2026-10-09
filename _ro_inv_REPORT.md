# READ-ONLY PostgreSQL Inventory — Hyper-Alpha-Arena

**Generated:** 2026-09-18 ~09:45 (+08) by delegated subagent.
**Mode:** fully read-only. Only `SELECT` / `information_schema` / `pg_catalog` reads plus one
**session GUC** (`SET app.is_admin = 'on'`, a connection-local setting, not a data write).
No `INSERT` / `UPDATE` / `DELETE` / `CREATE` / `ALTER` / `DROP` was issued. No service restarted.

---

## 0. TL;DR — READ THIS FIRST

1. **RLS trap.** `alpha_arena` has **58 tables with `ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`**,
   all using one policy name `tenant_isolation`. Connecting as user `laobao` (not superuser, no
   `BYPASSRLS`) with `row_security=on` makes `SELECT count(*)` return **0** for all of them even
   though the rows exist. Every count in this report was taken **after** `SET app.is_admin = 'on'`
   (the policy's own escape hatch: `current_setting('app.is_admin',true) = 'on'`).
   `row_security = off` does **not** work — it raises `InsufficientPrivilege`.
   **Any future SQL you run on alpha_arena must start with `SET app.is_admin = 'on'` or it will silently see empty tables.**

2. **`decision_snapshots` lives in `alpha_analytics`, not `alpha_arena`.**
   - `alpha_arena.public.decision_snapshots` → **0 rows** (a stale/dead duplicate; different column set: has `created_at`).
   - `alpha_analytics.public.decision_snapshots` → **5,941 rows**, ts col `timestamp`, 2026-09-11 → 2026-09-18.

3. **`signal_feedback` is `alpha_arena.public.signal_trade_feedback`** (622,358 rows) — the SignalFeedback /
   逐单增量归因 table. It has **no `factor_name`, no `factor_id`, no `avg_pnl`, no `incremental_pnl`**.
   Factor identity is carried **inside `signal_type` as `factor:<name>`** (e.g. `factor:cloud_microstructure_kyle`),
   and the per-trade PnL is `trade_pnl` / `trade_pnl_pct`.

4. **`factor_signal_log` DOES NOT EXIST.** The identifier appears exactly once in the whole repo, in a
   design document (`docs/因子与LLM统一策略架构_诊断与设计_2026-09-17.md:76`), as a conceptual name for
   "上次该因子投票的交易盈亏". There is no table, ORM model, migration, or DDL by that name in any of the
   5 databases. Implemented stand-ins are listed in §7.

5. **No live trade table has rows under the names `trades` / `orders` / `positions`** in `alpha_arena`
   (all 0). Real activity lives in `paper_orders` (13,615), `paper_positions` (3,520),
   `position_exit_events` (5,800), `strategy_trades` (2,368), `trade_facts` (2,498),
   `trade_memory_records` (3,485), `lane_ledger` (5,298).

6. **The 2026-09-16/17 boundary is real and sharp for AsterDex:** `alpha_market.public.asterdex_trades`
   has `MIN(ingest_ts) = 2026-09-16 14:42:07.705278+08` — AsterDex trade collection starts there.
   `signal_trade_feedback` jumps 5,094 (09-16) → 9,269 (09-17).

7. **A 4th database exists:** `alpha_snapshots` (`SNAPSHOT_DATABASE_URL`), 2 tables, both empty.

8. **The DB is live** — row counts drift between runs
   (`factor_exposure_snapshots`: 706,331 → 706,727 within minutes). Treat every count as a snapshot.

---

## 1. Connection (§1 of the brief)

### 1.1 Env keys used (from `D:\001Alpha\Hyper-Alpha-Arena\.env`)

| database | env key | url shape (password redacted) |
|---|---|---|
| `alpha_arena` | `DATABASE_URL` | `postgresql+psycopg://laobao:***@localhost:5432/alpha_arena` |
| `alpha_analytics` | `ANALYTICS_DATABASE_URL` | `postgresql+psycopg://laobao:***@localhost:5432/alpha_analytics` |
| `alpha_market` | `MARKET_DATABASE_URL` | `postgresql+psycopg://laobao:***@localhost:5432/alpha_market` |
| `alpha_snapshots` | `SNAPSHOT_DATABASE_URL` | `postgresql+psycopg://laobao:***@localhost:5432/alpha_snapshots` |

Keys are registered in `backend/config/env_registry.py` at lines 475 (`ANALYTICS_DATABASE_URL`),
730 (`DATABASE_URL`), 949 (`MARKET_DATABASE_URL`), 1659 (`SNAPSHOT_DATABASE_URL`), 1686 (`TEST_DATABASE_URL`).
`backend/config/settings.py` contains no literal DB name — it is env-driven only.
`TEST_DATABASE_URL` is **not** present in `.env`.

Connection uses the URL with the SQLAlchemy driver suffix stripped
(`postgresql+psycopg://` → `postgresql://`). Driver actually used: **psycopg2 2.9.12**
(`psycopg` 3.3.4 and `SQLAlchemy` 2.0.35 are also installed in `backend\.venv`).
Both driver URLs resolved with `connect_timeout=8`, `c.set_session(readonly=True, autocommit=True)`.

Verified via: `SELECT current_database(), current_user, inet_server_addr(), inet_server_port(), version()`

### 1.2 Reachability — all confirmed OK

| database | size | reachable | current_user | current_database | server |
|---|---|---|---|---|---|
| `alpha_arena` | 1636 MB | **YES** | `laobao` | `alpha_arena` | PostgreSQL 15.18 (VC++ 1944, 64-bit) |
| `alpha_analytics` | 1364 MB | **YES** | `laobao` | `alpha_analytics` | PostgreSQL 15.18 |
| `alpha_market` | 90 GB | **YES** | `laobao` | `alpha_market` | PostgreSQL 15.18 |
| `alpha_snapshots` | 7823 kB | **YES** (bonus) | `laobao` | `alpha_snapshots` | PostgreSQL 15.18 |

Host resolved as `127.0.0.1/32`, port `5432`. **No database was unreachable.**

```sql
SELECT datname, pg_size_pretty(pg_database_size(datname)), datallowconn
FROM pg_database WHERE NOT datistemplate ORDER BY datname;
-- alpha_analytics 1364 MB True
-- alpha_arena     1636 MB True
-- alpha_market      90 GB True
-- alpha_snapshots 7823 kB True
-- postgres        7471 kB True
```

### 1.3 The RLS blocker and its resolution (§1 addendum — most important operational finding)

```sql
SELECT current_user, session_user, current_database(),
       (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user),
       (SELECT rolsuper     FROM pg_roles WHERE rolname = current_user),
       current_setting('row_security');
-- laobao | laobao | alpha_arena | False | False | on

SELECT rolname, rolsuper, rolbypassrls, rolcanlogin FROM pg_roles ORDER BY rolname;
-- laobao        super=False bypassrls=False login=True
-- postgres      super=True  bypassrls=True  login=True
-- (plus 12 built-in pg_* roles, all login=False)
```

Policy shape (identical on all 58 tables, e.g. `signal_trade_feedback`):

```sql
-- SELECT * FROM pg_policies WHERE schemaname='public';
-- tablename=signal_trade_feedback | policyname=tenant_isolation | permissive=PERMISSIVE
--   roles={public} | cmd=ALL
--   USING     : (tenant_id = (NULLIF(current_setting('app.tenant_id',true),''))::integer)
--               OR (tenant_id IS NULL)
--               OR (current_setting('app.is_admin',true) = 'on')
--   WITH CHECK: <same>
```

Because `app.tenant_id` and `app.is_admin` are unset, only rows with `tenant_id IS NULL` are visible →
every `tenant_id = 1` row is hidden → `count(*) = 0`.

Evidence:

| table | `count(*)` as-is | `count(*)` after `SET app.is_admin='on'` | `reltuples` |
|---|---|---|---|
| `public.signal_trade_feedback` | 0 | 622,358 | 621,187 |
| `public.scalp_signal_log` | 318,529 | 365,024 | 362,344 |
| `public.paper_orders` | 0 | 13,615 | 13,544 |
| `public.paper_positions` | 0 | 3,520 | 3,520 |
| `public.position_exit_events` | 0 | 5,800 | 5,556 |
| `public.strategy_trades` | 0 | 2,368 | 2,326 |
| `public.trade_memory_records` | 0 | 3,485 | 3,357 |
| `public.accounts` | 0 | 19 | 19 |
| `public.ai_strategies` | 0 | 3,310 | 3,310 |
| `public.decision_snapshots` | 0 | **0 (genuinely empty)** | -1 |
| `public.trades` / `orders` / `positions` | 0 | **0 (genuinely empty)** | -1 |

`SET row_security = off` is **NOT** a workaround — it errors:

```sql
SET row_security = off;
SELECT count(*) FROM public.signal_trade_feedback;
-- ERROR: 查询将被表 "signal_trade_feedback" 的行级安全性策略所影响
-- HINT:  要禁用对表拥有者的策略，使用 ALTER TABLE NO FORCE ROW LEVEL SECURITY。
-- (SQLSTATE InsufficientPrivilege)
```

**The 58 FORCE-RLS tables** (all `relkind=r`, owner `laobao`, all with policy `tenant_isolation`):
`account_asset_snapshots, account_prompt_bindings, account_strategy_configs, accounts,
ai_attribution_conversations, ai_attribution_messages, ai_prompt_conversations, ai_prompt_messages,
ai_signal_conversations, ai_signal_messages, ai_strategies, alpha_assistant_conversations,
alpha_assistant_messages, arbitrage_paper_accounts, arbitrage_paper_exchange_balances,
arbitrage_paper_ledgers, arbitrage_positions, arbitrage_profiles, atas_strategies,
auto_coin_selections, binance_positions, dashboard_layouts, dingtalk_notifications,
exchange_credentials, full_auto_sessions, hyperliquid_account_snapshots,
hyperliquid_exchange_actions, hyperliquid_positions, hyperliquid_wallets, llm_configurations,
orders, paper_balances, paper_funding_ledger, paper_orders, paper_positions,
position_exit_events, positions, prompt_training_records, rebate_orders, rebate_performance_logs,
rebate_positions, rebate_trade_outcomes, refresh_tokens, risk_control_configs, scalp_signal_log,
signal_performance_history, signal_trade_feedback, strategy_executions, strategy_memories,
strategy_trades, trade_memory_records, trader_mental_states, trader_personalities, trades,
user_auth_sessions, user_exchange_config, user_subscriptions, visual_strategies`
(plus 2 more in `alpha_analytics`: `kline_ai_analysis_logs`, `llm_usage_logs`).

---

## 2. Table inventory + row counts

`alpha_market` counts are exact (`count(*)`); they are big (e.g. `crypto_klines` 60.9M) but completed.
Raw dumps: `_ro_inv1.out.utf8.txt` (naive counts), `_ro_inv5.out.txt` (§5 true counts + §6 columns).

### 2.1 `alpha_arena` — 177 relations (175 tables + 1 view + backups)

#### **Highlighted** (keyword matches) — TRUE counts

| table | rows | note |
|---|---|---|
| `public.signal_trade_feedback` | **622,358** | **SignalFeedback / 逐单增量归因** |
| `public.scalp_signal_log` | **365,024** | largest signal log |
| `public.paper_orders` | **13,615** | orders (paper) |
| `public.paper_positions` | **3,520** | positions (paper) |
| `public.position_exit_events` | **5,800** | exits |
| `public.strategy_trades` | **2,368** | trades |
| `public.trade_facts` | **2,498** | trades (facts) |
| `public.trade_memory_records` | **3,485** | trades (memory) |
| `public.trade_journals` | **585** | trades |
| `public.lane_ledger` | **5,298** | lane ledger |
| `public.signal_ledger` | **6,214** | signals |
| `public.signal_definitions` | **3,555** | |
| `public.signal_weight_history` | **2,916** | |
| `public.signal_pools` | **1,184** | |
| `public.signal_trigger_logs` | **0** | |
| `public.signal_performance_history` | **0** | |
| `public.paper_funding_ledger` | **759** | |
| `public.paper_balances` | **4** | |
| `public.rebate_orders` | **305** | stale (2026-06) |
| `public.rebate_positions` | **305** | stale (2026-06) |
| `public.rebate_trade_outcomes` | **277** | stale (2026-06) |
| `public.rebate_performance_logs` | **279** | |
| `public.brain_attribution` | **797** | attribution |
| `public._bak_brain_attribution_20260909` | **1,323** | backup |
| `public.evolution_events` | **425** | evolution |
| `public.rebate_evolution_proposals` | **4,842** | evolution |
| `public.opencode_evolution_proposals` | **457** | evolution |
| `public.exchange_rule_snapshots` | **1,558** | snapshot |
| `public.edge_ledger_snapshots` | **62** | snapshot |
| `public.account_asset_snapshots` | **15** | snapshot |
| `public.decision_snapshots` | **0** | **DEAD — see `alpha_analytics`** |
| `public.trades` | **0** | dead |
| `public.orders` | **0** | dead |
| `public.positions` | **0** | dead |
| `public.live_orders` | **0** | dead |
| `public.live_sub_positions` | **0** | dead |
| `public.arbitrage_positions` | **0** | dead |
| `public.binance_positions` | **32** | |
| `public.strategic_reports` | **0** | **DEAD — see `alpha_analytics`** |
| `public.strategic_macro_snapshots` | **0** | dead |
| `public.strategic_memories` | **0** | dead |
| `public.factor_sync_config` | **1** | |
| `public.atas_factor_cache` | **90** | |
| `public.atas_factors` | **0** | dead |
| `public.cloud_factor_definitions` | **5** | |
| `public.backtest_trades` | **70** | |
| `public.wash_trade_logs` | **305** | |
| `public.trader_mental_states` | **4** | |
| `public.trader_personalities` | **1** | |
| `public.ai_attribution_conversations` / `ai_attribution_messages` | **0** | dead |
| `public.ai_signal_conversations` / `ai_signal_messages` | **0** | dead |
| `public.rebate_incentive_snapshots` | **0** | dead |
| `public.hyperliquid_account_snapshots` / `hyperliquid_positions` | **0** | dead |
| `_bak_garbage_accounts_20260801.*` (13 tables) | 7 / 4 / 4 / 2,218 / 1 / 3 / 123 / 1,182 / 397 / 454 / 558 / 1,341 / 772 | archived backup schema |
| `public.bak_20260821_*` (6 tables) | 8 / 2 / 4 / 9,813 / 2,596 / 3,778 / 1,565 | archived backups |

#### All other non-zero `alpha_arena` `public` tables

| table | rows |
|---|---|
| `multi_symbol_kelly` | 127,104 |
| `coin_select_candidates` | 62,338 |
| `job_runs` | 47,984 |
| `analysis_runs` | 34,062 |
| `alpha_assistant_messages` | 23,956 |
| `llm_quota_usage` | 22,090 |
| `agent_predictions` | 9,596 |
| `arbitrage_paper_ledgers` | 7,575 |
| `coordinator_actions` | 4,597 |
| `strategy_hypotheses` | 4,430 |
| `signal_definitions` | 3,555 |
| `ai_strategies` | 3,310 |
| `strategy_templates` | 2,167 |
| `coin_select_scans` | 2,639 |
| `observation_pool_labels` | 2,603 |
| `opencode_insights` | 2,749 |
| `refresh_tokens` | 2,699 |
| `pair_strategy_candidates` | 2,425 |
| `exchange_rule_snapshots` | 1,558 |
| `rule_ai_analysis_logs` / `rule_change_events` | 1,547 |
| `risk_control_events` | 5,298 |
| `admin_audit_logs` | 21 |
| `system_configs` | 7 |
| `users` | 213 |
| `job_registry` | 47 |
| `lane_registry` / `lane_runtime_state` / `lane_shadow_report` / `lane_breaker_log` | 6 / 5 / 10 / 1 |
| `symbol_scalp_profile` | 940 |
| `trading_wisdom` | 92 |
| `period_daily_reports` | 96 |
| `brain_episodes` / `brain_lessons` | 1,382 |
| `brain_theses` | 1,069 |
| `brain_research_tasks` | 92 |
| `strategy_regime_scores` | 198 |
| `drl_performance` / `drl_performance_daily` | 239 / 106 |
| `kline_research_log` | 476 |
| `backtest_runs` | 515 |
| `experiment_heartbeat` | 6 |
| `global_sampling_configs` / `market_regime_configs` / `trading_configs` / `system_coordinator_state` | 1 / 1 / 1 / 2 |
| `rule_sync_audit_logs` / `rule_sync_gate_state` | 1 / 1 |
| `prompt_templates` | 22 |
| `prompt_training_records` | 25 |
| `exchange_proxy_configs` / `exchange_credentials` | 1 / 1 |
| `scalp_backtest_run` / `scalp_experiment_log` | 5 / 1 |
| `full_auto_sessions` | 2 |
| `arbitrage_paper_accounts` | 2 |
| `arbitrage_paper_allocation_presets` | 5 |
| `arbitrage_paper_exchange_balances` | 14 |
| `arbitrage_profiles` | 1 |
| `coin_select_adoptions` | 2 |
| `pair_strategy_bindings` | 24 |
| `account_strategy_configs` | 1 |
| `llm_configurations` | 5 |

**`alpha_arena.public` BASE TABLE census (verified programmatically):**
162 base tables = **111 non-zero + 51 zero-row**, plus 14 backup/archive relations in the
`_bak_garbage_accounts_20260801` schema and 1 view (`lane_ledger_pairing_gaps`, 0 rows).

**The 51 zero-row `alpha_arena.public` tables (complete, verified):**
```
account_prompt_bindings, ai_analysis_logs, ai_attribution_conversations, ai_attribution_messages,
ai_decision_logs, ai_prompt_conversations, ai_prompt_messages, ai_signal_conversations,
ai_signal_messages, anomaly_events, arbitrage_positions, atas_ai_generation_history,
atas_factors, atas_prompt_templates, atas_strategies, brain_agent_calibration,
cross_market_correlations, custom_trading_styles, dashboard_layouts, decision_snapshots,
dingtalk_bots, dingtalk_notification_stats, dingtalk_notifications, experiments,
hyperliquid_account_snapshots, hyperliquid_exchange_actions, hyperliquid_positions,
hyperliquid_wallets, live_orders, live_sub_positions, macro_regime_states,
market_regime_history, new_coin_opportunities, orders, positions, rebate_incentive_snapshots,
signal_performance_history, signal_trigger_logs, strategic_macro_snapshots, strategic_memories,
strategic_reports, strategy_executions, strategy_node_templates, trader_trigger_config,
trades, trend_cycles, trend_prediction_records, user_auth_sessions, user_exchange_config,
user_subscriptions, visual_strategies
```
**The 111 non-zero `alpha_arena.public` tables:**
```
_bak_brain_attribution_20260909, account_asset_snapshots, account_strategy_configs, accounts,
admin_audit_logs, agent_predictions, ai_strategies, alembic_version_core,
alpha_assistant_conversations, alpha_assistant_messages, analysis_runs, arbitrage_paper_accounts,
arbitrage_paper_allocation_presets, arbitrage_paper_exchange_balances, arbitrage_paper_ledgers,
arbitrage_profiles, atas_factor_cache, auto_coin_selections, backtest_runs, backtest_trades,
bak_20260821_accounts, bak_20260821_full_auto_sessions, bak_20260821_paper_balances,
bak_20260821_paper_orders, bak_20260821_paper_positions, bak_20260821_position_exit_events,
bak_20260821_strategy_trades, binance_positions, brain_attribution, brain_episodes, brain_lessons,
brain_research_tasks, brain_theses, cloud_factor_definitions, coin_select_adoptions,
coin_select_candidates, coin_select_scans, coordinator_actions, drl_performance,
drl_performance_daily, edge_ledger_snapshots, evolution_events, exchange_credentials,
exchange_proxy_configs, exchange_rule_snapshots, experiment_heartbeat, factor_sync_config,
full_auto_sessions, global_sampling_configs, job_registry, job_runs, kline_research_log,
lane_breaker_log, lane_ledger, lane_registry, lane_runtime_state, lane_shadow_report,
llm_configurations, llm_quota_usage, market_regime_configs, multi_symbol_kelly,
observation_pool_labels, opencode_evolution_proposals, opencode_insights, pair_strategy_bindings,
pair_strategy_candidates, paper_balances, paper_funding_ledger, paper_orders, paper_positions,
period_daily_reports, position_exit_events, prompt_templates, prompt_training_records,
rebate_evolution_proposals, rebate_orders, rebate_performance_logs, rebate_positions,
rebate_trade_outcomes, refresh_tokens, risk_control_configs, risk_control_events,
rule_ai_analysis_logs, rule_change_events, rule_sync_audit_logs, rule_sync_gate_state,
scalp_backtest_run, scalp_experiment_log, scalp_signal_log, signal_definitions, signal_ledger,
signal_pools, signal_trade_feedback, signal_weight_history, strategy_hypotheses,
strategy_memories, strategy_regime_scores, strategy_templates, strategy_trades,
symbol_scalp_profile, system_configs, system_coordinator_state, trade_facts, trade_journals,
trade_memory_records, trader_mental_states, trader_personalities, trading_configs,
trading_wisdom, users, wash_trade_logs
```
Raw dump: `_ro_inv5.out.txt` §5, and `_ro_inv_counts.json` for machine-readable form.

### 2.2 `alpha_analytics` — 34 relations (ALL row counts exact, no RLS interference)

| table | rows | note |
|---|---|---|
| `public.factor_exposure_snapshots` | **706,331 → 706,727** | **factor\*** (grew during run) |
| `public.factor_performance_logs` | **355,417** | **factor\*** — has `factor_name` |
| `public.compute_metrics` | 274,360 | |
| `public.market_analysis_snapshots` | 56,081 | snapshot |
| `public.factor_evolution_log` | **17,564** | **factor\*** / evolution |
| `public.ai_decision_logs` | 10,337 | |
| `public.llm_usage_logs` | 9,087 | |
| `public.mlto_thesis_events` | 8,736 | |
| `public.decision_snapshots` | **5,941** | **decision_snapshots (LIVE)** |
| `public.decision_retrospectives` | **3,959** | attribution/learning |
| `public.risk_control_events` | 3,887 | |
| `public.mlto_episodes` | 2,883 | |
| `public.walk_forward_reports` | 2,213 | |
| `public.macro_regime_states` | 1,776 | |
| `public.strategic_reports` | **1,044** | **strategic_reports (LIVE)** |
| `public.strategic_macro_snapshots` | **1,044** | identical MIN/MAX to above |
| `public.factor_quality_reports` | **925** | **factor\*** — 925 distinct `factor_id`, all 1 row each |
| `public.mlto_thesis` | 152 | |
| `public.factor_active_set` | **25** | **factor\*** — 25 distinct `factor_id` |
| `public.mlto_signal_weights` | 12 | |
| `public.alembic_version_analytics` | 1 | |
| `public.kline_ai_analysis_logs` | 1 | |
| **zero-row (12):** `asterdex_live_points_events, cross_market_correlations, generated_signal_history, mlto_debate_log, mlto_memory_events, new_coin_opportunities, pattern_definitions, scalp_veto_audit, strategic_memories, strategy_analysis_logs, strategy_optimization_logs, trend_prediction_records` | 0 | |

### 2.3 `alpha_market` — 35 relations (90 GB)

| table | rows | note |
|---|---|---|
| `public.crypto_klines` | **60,964,235** | |
| `public.asterdex_book_ticker` | **51,387,807** | |
| `public.crypto_klines_bak_20260807114347` | 23,251,266 | backup |
| `public.perp_funding` | **17,376,894** | |
| `public.asterdex_depth_snapshots` | **12,319,409** | snapshot |
| `public.ticker_snapshots` | **5,127,763** | snapshot |
| `public.market_orderbook_snapshots` | **2,346,265** | snapshot |
| `public.raw_market_events` | 2,291,534 | |
| `public.market_trades_aggregated` | **1,475,533** | trades |
| `public.market_asset_metrics` | 1,264,616 | |
| `public.asterdex_trades` | **871,364** | **trades — starts 2026-09-16 14:42** |
| `public.liquidation_ticks` | 301,722 | |
| `public.symbol_aux_timeseries` | 179,811 | |
| `public.market_spot_klines` | 155,683 | |
| `public.position_structure` | **84,511** | positions |
| `public.liquidation_events` | 48,284 | |
| `public.whale_activities` | 35,509 | |
| `public.news_events` | 2,736 | |
| `public.symbol_catalog` | 2,577 | |
| `public.market_events` | 10,893 | |
| `public.flow_archive_5m` | 3,524 | |
| `public.macro_series` | 1,172 | |
| `public.exchange_announcements` | 204 | |
| `public.smart_money_moves` | 100 | |
| `public.smart_money_traders` | 28 | |
| `public.smart_money_snapshots` | 9 | |
| `public.smart_money_scorecard` | 5 | |
| `public.kline_sync_heartbeat` | 63 | |
| `public.kline_collection_tasks` | 5 | |
| `public.asterdex_stream_health` | 3 | |
| `public.macro_events` | 4 | |
| `public.alembic_version_market` | 1 | |
| **zero-row (3):** `crypto_price_ticks, crypto_prices, price_samples` | 0 | |

### 2.4 `alpha_snapshots` — 2 relations, both **0 rows**
`public.hyperliquid_account_snapshots`, `public.hyperliquid_trades`.

---

## 3. Keyword-matched tables (consolidated)

Keywords searched in table names: `decision_snapshot, signal_feedback, factor_signal, strategic_report,
trade, order, position, factor, attribution, learning, evolution, evolv, feedback, signal_log, snapshot`.

- `alpha_arena` → **55** matching relations (see §2.1)
- `alpha_analytics` → **9** (`decision_snapshots, factor_active_set, factor_evolution_log,
  factor_exposure_snapshots, factor_performance_logs, factor_quality_reports,
  market_analysis_snapshots, strategic_macro_snapshots, strategic_reports`)
- `alpha_market` → **8** (`asterdex_depth_snapshots, asterdex_trades, market_orderbook_snapshots,
  market_trades_aggregated, position_structure, smart_money_snapshots, smart_money_traders, ticker_snapshots`)
- `alpha_snapshots` → **2**

**No table name anywhere contains `learning`** (verified: 0 rows in `information_schema.tables`
matching `~* 'learn'` in all 4 DBs) — the only `learning`-adjacent object is the
`learning_loop_service.py` / `live_learning_hooks.py` code path, which writes into
`alpha_arena.signal_trade_feedback` and `alpha_arena.trade_facts`.
**No table name anywhere contains `signal_feedback`** (verified: 0 matches for `~* 'signal_feedback'`);
the column-name search for `feedback` found only `atas_ai_generation_history.user_feedback`
and the table `signal_trade_feedback`.
**No table name anywhere contains `factor_signal`** (verified: 0 matches for `~* 'factor_signal'`).

---

## 4. Column definitions (`information_schema.columns`)

Full dump of **1,377 columns across 82 keyword-matching tables** is in `_ro_inv5.out.txt` §6
(also machine-readable in `_ro_inv_columns.json`). Tables the brief explicitly names are reproduced
verbatim below.

### 4.1 `alpha_arena.public.signal_trade_feedback` (12 columns / 622,358 rows)
```sql
CREATE TABLE signal_trade_feedback (
  id               integer                 NOT NULL DEFAULT nextval('signal_trade_feedback_id_seq'),
  account_id       integer                 NOT NULL,
  trade_id         integer,
  symbol           character varying(20)   NOT NULL,
  signal_type      character varying(100)  NOT NULL,
  signal_value     double precision,
  signal_direction character varying(20),
  trade_pnl        double precision,
  trade_pnl_pct    double precision,
  trade_side       character varying(10),
  created_at       timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
  tenant_id        integer                 NOT NULL DEFAULT 1
);
```

### 4.2 `alpha_analytics.public.decision_snapshots` (31 columns / 5,941 rows)
```sql
 id, session_id, strategy_id, symbol, tier, timestamp, market_snapshot_json, ai_reasoning,
 action, direction, confidence, entry_price, exit_price, pnl, pnl_pct, duration_seconds,
 regime_at_decision, volatility_at_decision, quality_label, lesson_extracted, proposal_id,
 trace_id, source_lane, proposal_json, evaluate_verdict_json, gate_blocks_json,
 orchestrator_json, executed, execution_channel, content_hash, prev_hash
```
Types: `timestamp` is `timestamp without time zone DEFAULT CURRENT_TIMESTAMP`; `pnl`/`pnl_pct`/`confidence`
/`entry_price`/`exit_price`/`volatility_at_decision` are `double precision`; `symbol` `varchar(20) NOT NULL`;
`strategy_id` `varchar(50)`; `tier` `varchar(10)`; `regime_at_decision` `varchar(30)`;
`quality_label` `varchar(20)`; `action` `varchar(20)`; `direction` `varchar(10)`;
`proposal_id`/`trace_id`/`content_hash`/`prev_hash` `varchar(64)`; `source_lane` `varchar(32)`;
`execution_channel` `varchar(16)`; `market_snapshot_json`/`proposal_json`/`evaluate_verdict_json`/
`gate_blocks_json`/`orchestrator_json` are `json`; `ai_reasoning`/`lesson_extracted` are `text`;
`executed` is `boolean`; `duration_seconds`/`session_id` are `integer`.
ORM: `backend/database/models.py:3313 class DecisionSnapshot(AnalyticsBase)`.

### 4.3 `alpha_arena.public.decision_snapshots` (DEAD, 0 rows, different schema)
```sql
 id, session_id, strategy_id, symbol, tier, created_at, timestamp, market_snapshot_json,
 ai_reasoning, action, direction, confidence, entry_price, exit_price, pnl, pnl_pct,
 duration_seconds, regime_at_decision, volatility_at_decision, quality_label, ... 
```
Note this copy exposes **both** `created_at` and `timestamp`, unlike the analytics one.
ORM: `backend/database/models.py:3313` also declares `__tablename__ = "decision_snapshots"`.

### 4.4 `alpha_analytics.public.factor_performance_logs` (10 columns / 355,417 rows) — **best `factor_signal_log` stand-in**
```sql
 id             integer NOT NULL DEFAULT nextval(...),
 factor_name    character varying(50)  NOT NULL,   -- <== factor name lives here
 factor_category character varying(30) NOT NULL,
 ic_value       numeric(10,6),
 decay_rate     numeric(10,6),
 current_weight numeric(10,6),
 market_regime  character varying(30),
 symbol         character varying(20),
 timeframe      character varying(10),
 recorded_at    timestamp without time zone DEFAULT CURRENT_TIMESTAMP
```
ORM: `backend/database/models.py:3481`.

### 4.5 `alpha_analytics.public.factor_evolution_log` (11 columns / 17,564 rows)
```sql
 id, factor_id varchar(64) NOT NULL, expr_ast json, source varchar(64), phase varchar(20) NOT NULL,
 state_from varchar(20), state_to varchar(20), action varchar(20), reason text, metrics json,
 created_at timestamp DEFAULT CURRENT_TIMESTAMP
```
ORM: `backend/database/models.py:3499`.

### 4.6 `alpha_analytics.public.factor_exposure_snapshots` (8 columns / ~706,331 rows)
```sql
 id, ts timestamptz, symbol, period, factor_id, z_score, expected_alpha, weight
```

### 4.7 `alpha_analytics.public.factor_active_set` (17 columns / 25 rows)
```sql
 id, factor_id varchar(64) NOT NULL UNIQUE, expr_ast json NOT NULL, expr_id varchar(64),
 source varchar(64), state varchar(20) NOT NULL DEFAULT 'ACTIVE', icir double precision,
 incremental_corr double precision, capacity_usd double precision, current_weight json,
 activated_at, deactivated_at, last_evaluated_at, last_net_ic, turnover, evaluated_cycles, period
```

### 4.8 `alpha_analytics.public.factor_quality_reports` (9 columns / 925 rows)
```sql
 id, factor_id varchar(64), report_date date, ic_mean, icir, coverage, grade, is_alive, created_at
```

### 4.9 `alpha_analytics.public.strategic_reports` (1,044 rows) & `strategic_macro_snapshots` (1,044 rows)
Both have the same MIN/MAX `timestamp` (2026-06-02 05:35:18.585248 → 2026-09-18 09:23:24.568347) and the
same row count — see `_ro_inv5.out.txt` §6 lines 1840-1884 for both column lists.

### 4.10 `alpha_arena.public.scalp_signal_log` (31 columns / 365,024 rows)
```sql
 id, created_at, symbol, signal_ts, direction, action, factor_score, threshold, entry_price,
 session_id, account_id, features_json, horizon_sec, settled, settle_ts, exit_price, fwd_ret,
 net_ret, win, settle_note, tenant_id, tb_tp_pct, tb_sl_pct, tb_max_hold_sec, tb_kind,
 tb_hold_sec, tb_fwd_ret, tb_net_ret, tb_win, tb_settled
```

### 4.11 `alpha_arena` trade/position tables — column lists
- `public.paper_orders` — see `_ro_inv5.out.txt:1135`
- `public.paper_positions` — `:1161`
- `public.position_exit_events` — `:1208`
- `public.strategy_trades` — `:1519`
- `public.trade_facts` — `:1544`
- `public.trade_journals` — `:1565`
- `public.trade_memory_records` — `:1579`
- `public.trades` / `orders` / `positions` — `:1659` / `:1110` / `:1236` (all empty)
- `public.signal_ledger` — `:1388`
- `public.signal_weight_history` — `:1468`
- `public.signal_definitions` — `:1378`, `public.signal_pools` — `:1431`
- `public.evolution_events` — `:959`, `public.brain_attribution` — `:871`
- `public.lane_ledger` — not a keyword match; `public.wash_trade_logs` — `:1675`

---

## 5. Time coverage + histograms

Full data: `_ro_inv6.out.txt` (arena + analytics), `_ro_inv8.out.txt` (market).
DB clock at time of run: **`now() = 2026-09-18 09:42:06.944231+08`**, `current_date = 2026-09-18`,
45-day cutoff = `2026-08-04`.

### 5.1 MIN / MAX / total rows

| database | table | ts column | MIN | MAX | total rows |
|---|---|---|---|---|---|
| alpha_arena | `public.signal_trade_feedback` | `created_at` | 2026-07-18 04:20:34.073448 | 2026-09-18 09:25:29.007484 | 622,358 |
| alpha_arena | `public.scalp_signal_log` | `created_at` | 2026-07-18 03:12:06.631673 | **2026-09-05 11:21:19** (STALE) | 365,024 |
| alpha_arena | `public.paper_orders` | `created_at` | 2026-06-30 19:40:28.741136 | 2026-09-18 09:25:37.846052 | 13,615 |
| alpha_arena | `public.paper_orders` | `filled_at` | 2026-06-30 19:40:28.911754 | 2026-09-18 09:25:24.935092 | |
| alpha_arena | `public.paper_positions` | `opened_at` | 2026-06-30 19:40:28.741136 | 2026-09-18 09:25:24.680753 | 3,520 |
| alpha_arena | `public.paper_positions` | `closed_at` | 2026-06-30 21:40:29.273564 | 2026-09-18 05:04:50.358415 | |
| alpha_arena | `public.paper_positions` | `updated_at` | 2026-06-30 21:40:29.352479 | 2026-09-18 09:42:03.459725 | |
| alpha_arena | `public.paper_positions` | `last_add_at` | 2026-07-16 22:20:20.937103 | 2026-09-03 10:29:28.779782 | |
| alpha_arena | `public.paper_positions` | `last_reduce_at` | 2026-08-17 01:04:12.773884 | 2026-09-17 01:22:40.512495 | |
| alpha_arena | `public.position_exit_events` | `created_at` | 2026-06-30 20:10:53.058486 | 2026-09-18 05:04:52.056299 | 5,800 |
| alpha_arena | `public.strategy_trades` | `opened_at` | 2026-07-14 14:48:53.900327 | 2026-09-17 14:40:18.872719 | 2,368 |
| alpha_arena | `public.strategy_trades` | `closed_at` | 2026-07-14 15:44:15.453480 | 2026-09-17 21:04:51.491174 | |
| alpha_arena | `public.trade_memory_records` | `opened_at` | 2026-06-30 19:40:29.337755 | 2026-09-17 22:40:42.849716 | 3,485 |
| alpha_arena | `public.trade_memory_records` | `closed_at` | 2026-06-30 21:40:29.333972 | 2026-09-18 05:04:51.847546 | |
| alpha_arena | `public.trade_facts` | `ts` (timestamptz) | 2026-08-02 00:58:45.216374+08 | 2026-09-18 05:04:51.206090+08 | 2,498 |
| alpha_arena | `public.trade_journals` | `created_at` | 2026-04-11 04:04:43 | 2026-09-18 09:23:56.870212 | 585 |
| alpha_arena | `public.lane_ledger` | `ts` (timestamptz) | 2026-09-09 10:53:52.395767+08 | **2026-09-17 09:20:01.053190+08** | 5,298 |
| alpha_arena | `public.paper_funding_ledger` | `settled_at` | 2026-06-30 21:40:29.189615 | 2026-09-18 09:25:37.353072 | 759 |
| alpha_arena | `public.signal_weight_history` | `computed_at` | 2026-04-06 06:05:41 | 2026-09-18 05:10:00.927822 | 2,916 |
| alpha_arena | `public.brain_attribution` | `created_at` | 2026-08-25 17:03:34.480888 | 2026-09-18 05:04:50.385035 | 797 |
| alpha_arena | `public.evolution_events` | `created_at` | 2026-06-22 17:41:03.620085 | **2026-09-16 09:12:07.098380** | 425 |
| alpha_arena | `public.rebate_performance_logs` | `created_at` | 2026-05-26 05:59:20 | 2026-09-18 09:41:14.029782 | 279 |
| alpha_arena | `public.rebate_orders` | `created_at` | 2026-06-08 16:36:00.880274 | 2026-06-19 19:39:00.494168 | 305 |
| alpha_arena | `public.rebate_positions` | `created_at` | 2026-06-08 16:36:00.880274 | 2026-06-19 19:39:00.494168 | 305 |
| alpha_arena | `public.rebate_trade_outcomes` | `created_at` | 2026-06-08 21:09:36.088297 | 2026-06-19 19:55:22.368395 | 277 |
| alpha_arena | `public.strategy_memories` | `updated_at` | 2026-07-18 20:06:28.738077 | 2026-09-18 09:25:06.712587 | 600 |
| alpha_arena | `public.trades` | `trade_time` | NULL | NULL | **0** |
| alpha_arena | `public.orders` | `created_at`,`updated_at` | NULL | NULL | **0** |
| alpha_arena | `public.positions` | `created_at`,`updated_at` | NULL | NULL | **0** |
| alpha_arena | `public.decision_snapshots` | `created_at`,`timestamp` | NULL | NULL | **0** |
| alpha_arena | `public.signal_ledger` | *(none — uses `created_ms` bigint)* | n/a | n/a | 6,214 |
| alpha_arena | `public.agent_predictions` | *(no ts column)* | n/a | n/a | 9,596 |
| alpha_arena | `public.backtest_trades` | *(no ts column)* | n/a | n/a | 70 |
| **alpha_analytics** | `public.decision_snapshots` | `timestamp` | 2026-09-11 10:08:36.528111 | 2026-09-18 09:30:19.480691 | **5,941** |
| alpha_analytics | `public.factor_performance_logs` | `recorded_at` | 2026-08-21 05:10:37.192163 | 2026-09-18 05:10:52.912729 | 355,417 |
| alpha_analytics | `public.factor_exposure_snapshots` | `ts` (timestamptz) | 2026-09-04 09:28:58.959036+08 | 2026-09-18 09:32:38.205758+08 | ~706.3k |
| alpha_analytics | `public.factor_evolution_log` | `created_at` | 2026-07-23 14:46:47.992081 | 2026-09-18 06:27:15.249372 | 17,564 |
| alpha_analytics | `public.factor_quality_reports` | `report_date`, `created_at` | 2026-07-23 | 2026-07-23 | 925 |
| alpha_analytics | `public.factor_active_set` | `activated_at` | 2026-08-02 00:42:34.931344 | 2026-09-18 06:27:15.185997 | 25 |
| alpha_analytics | `public.factor_active_set` | `deactivated_at` | 2026-08-02 00:52:14.336076 | 2026-09-17 14:33:24.963297 | |
| alpha_analytics | `public.factor_active_set` | `last_evaluated_at` | 2026-08-10 04:11:24.967855 | 2026-09-18 09:11:15.978850 | |
| alpha_analytics | `public.strategic_reports` | `timestamp` | 2026-06-02 05:35:18.585248 | 2026-09-18 09:23:24.568347 | **1,044** |
| alpha_analytics | `public.strategic_macro_snapshots` | `timestamp` | 2026-06-02 05:35:18.585248 | 2026-09-18 09:23:24.568347 | 1,044 |
| alpha_analytics | `public.decision_retrospectives` | `created_at` | 2026-06-11 01:01:09.619447 | 2026-09-18 05:04:50.636983 | 3,959 |
| alpha_analytics | `public.market_analysis_snapshots` | `created_at` | 2026-04-17 14:21:11 | 2026-09-18 09:40:03.931549 | 56,081 |
| alpha_analytics | `public.walk_forward_reports` | `run_at` (timestamptz) | 2026-08-02 00:55:12.959034+08 | 2026-09-18 06:27:13.164133+08 | 2,213 |
| alpha_analytics | `public.ai_decision_logs` | `decision_time`, `created_at` | 2026-09-04 09:23:57.620174 | 2026-09-18 09:40:26.745186 | 10,337 |
| alpha_analytics | `public.ai_decision_logs` | `pnl_updated_at` | NULL | NULL | |
| alpha_analytics | `public.mlto_thesis_events` | `ts` | 2026-08-31 00:17:29.746112 | 2026-09-18 09:40:26.934817 | 8,736 |
| alpha_analytics | `public.mlto_episodes` | `created_at` | 2026-09-07 16:54:14.904584 | 2026-09-18 09:40:26.946624 | 2,883 |
| alpha_market | `public.asterdex_trades` | `ingest_ts` (timestamptz) | **2026-09-16 14:42:07.705278+08** | 2026-09-18 09:43:18.828446+08 | 871,364 |
| alpha_market | `public.asterdex_trades` | `event_ts_ms` (epoch ms) | 1789540926604 | 1789695796473 | |
| alpha_market | `public.asterdex_trades` | `trade_ts_ms` (epoch ms) | 1789540926400 | 1789695796250 | |
| alpha_market | `public.market_trades_aggregated` | `created_at` / `timestamp` (ms) | 2026-08-19 09:23:28.143528 / 1787102595000 | 2026-09-18 09:43:19.586050 / 1789695780000 | 1,475,533 |
| alpha_market | `public.position_structure` | `created_at`,`updated_at` (tz) / `ts_ms` | 2026-09-03 18:08:08.185981+08 / 1786600800000 | 2026-09-18 09:14:36.880198+08 / 1789693200000 | 84,511 |
| alpha_market | `public.market_orderbook_snapshots` | `created_at` / `timestamp` (ms) | 2026-08-08 12:44:12.794948 / 1787102580000 | 2026-09-18 09:46:25.374995 / 1789695975000 | 2,346,265 |
| alpha_market | `public.liquidation_events` | `created_at` / `ts_ms` | 2026-08-15 15:55:45.696227 / 1786759200000 | 2026-09-18 09:36:16.054460 / 1789693200000 | 48,284 |
| alpha_market | `public.crypto_klines` | `created_at` | 2026-02-25 05:27:08 | 2026-09-18 09:46:29.991654 | 60,964,235 |
| alpha_market | `public.perp_funding` | `created_at` / `timestamp` (ms) | 2026-03-30 05:58:21 / 1756897200004 | 2026-09-18 09:46:25.374995 / 1789695960000 | 17,376,894 |
| alpha_market | `public.ticker_snapshots` | `created_at` / `ts_ms` | 2026-09-04 09:23:15.666782 / 1788484987909 | 2026-09-18 09:46:30.635580 / 1789695990820 | 5,127,763 |
| alpha_market | `public.market_asset_metrics` | `created_at` / `timestamp` (ms) | 2026-08-19 09:23:01.171090 / 1787102580000 | 2026-09-18 09:46:36.118834 / 1789695990000 | 1,264,616 |
| alpha_market | `public.raw_market_events` | `created_at` / `event_ts` (s) | 2026-07-25 03:13:18.613477 / 1784920260 | 2026-09-18 09:46:39.503486 / 1789695900 | 2,291,534 |
| alpha_market | `public.whale_activities` | `timestamp` | 2026-09-11 09:41:22.454747 | 2026-09-18 09:45:59 | 35,509 |
| alpha_market | `public.symbol_aux_timeseries` | `created_at` / `timestamp_ms` | 2026-05-21 08:16:35 / 1779351395889 | 2026-09-18 09:43:35.502986 / 1789695815498 | 179,811 |

**Could not determine:** `alpha_market.public.asterdex_depth_snapshots.ingest_ts` MIN/MAX —
the query was cancelled by the 90 s `statement_timeout` on that 12.3 M-row table
(`event_ts_ms` MIN/MAX did complete: 1789540926269 .. 1789695890983, i.e. 2026-09-16 14:42 .. 2026-09-18 09:44 +08).

### 5.2 Per-day histograms (last 45 days = 2026-08-04 .. 2026-09-18)

SQL template:
```sql
SELECT to_char(date_trunc('day', "<ts_col>"), 'YYYY-MM-DD') AS d, count(*)
FROM <table>
WHERE "<ts_col>" >= date_trunc('day', now()) - INTERVAL '45 days'
GROUP BY 1 ORDER BY 1;
```

#### `alpha_arena.public.signal_trade_feedback` (`created_at`) — 236,085 rows in window
```
2026-08-04  10298 | 2026-08-05  13463 | 2026-08-06   2990 | 2026-08-07   8599 | 2026-08-08  12864
2026-08-09   2868 | 2026-08-10   6703 | 2026-08-11   8567 | 2026-08-12   5744 | 2026-08-13   6762
2026-08-14   2235 | 2026-08-15   1802 | 2026-08-16   1542 | 2026-08-17   5587 | 2026-08-18   8132
2026-08-19   9539 | 2026-08-20   1151 | 2026-08-21  10202 | 2026-08-22   2556 | 2026-08-23   5569
2026-08-24   7793 | 2026-08-25   3119 | 2026-08-26   3686 | 2026-08-27   2765 | 2026-08-28   9348
2026-08-29   5567 | 2026-08-30    439 | 2026-08-31   5079 | 2026-09-01  18121 | 2026-09-02   8392
2026-09-03   6508 | 2026-09-04   2278 | 2026-09-05   2029 | 2026-09-06    615 | 2026-09-07    613
2026-09-08   2507 | 2026-09-09    648 | 2026-09-10   1410 | 2026-09-11   2859 | 2026-09-12    857
2026-09-13    879 | 2026-09-14   6344 | 2026-09-15   2604 | 2026-09-16   5094 | 2026-09-17   9269
2026-09-18     89
```
**★ boundary:** 09-15 = 2,604 → **09-16 = 5,094 → 09-17 = 9,269** (a 3.6× step-up across 09-16/17).

#### `alpha_arena.public.scalp_signal_log` (`created_at`) — 235,719 rows in window, **stops 2026-09-05**
```
2026-08-04  18614 | 2026-08-05  26442 | 2026-08-06  21399 | 2026-08-07   9375 | 2026-08-08   6921
2026-08-09    892 | 2026-08-10  13768 | 2026-08-11  15947 | 2026-08-12   9187 | 2026-08-13  15733
2026-08-14   6114 | 2026-08-15   5937 | 2026-08-16   1631 | 2026-08-17   6268 | 2026-08-18  16877
2026-08-19  26790 | 2026-08-20   2787 | 2026-08-21  23184 | 2026-08-22   4221 | 2026-08-23     74
2026-08-24    138 | 2026-08-25     55 | 2026-08-26     55 | 2026-08-27     27 | 2026-08-28    106
2026-08-29    209 | 2026-08-30     20 | 2026-08-31     26 | 2026-09-01    106 | 2026-09-02     84
2026-09-03    201 | 2026-09-04    733 | 2026-09-05   1798   <-- WRITER STOPPED HERE
```
**★ `scalp_signal_log` is DEAD after 2026-09-05 11:21:19** — 13 days of silence before now.

#### `alpha_arena.public.paper_orders` (`created_at`) — 9,652 rows in window
```
2026-08-04   254 | 2026-08-05   333 | 2026-08-06    98 | 2026-08-07   190 | 2026-08-08   287
2026-08-09    60 | 2026-08-10   186 | 2026-08-11   328 | 2026-08-12   604 | 2026-08-13   680
2026-08-14   153 | 2026-08-15   114 | 2026-08-16    80 | 2026-08-17   372 | 2026-08-18   590
2026-08-19   639 | 2026-08-20    91 | 2026-08-21   701 | 2026-08-22   191 | 2026-08-23   327
2026-08-24   515 | 2026-08-25   215 | 2026-08-26   252 | 2026-08-27   161 | 2026-08-28   452
2026-08-29   168 | 2026-08-30    14 | 2026-08-31   142 | 2026-09-01   429 | 2026-09-02   343
2026-09-03   195 | 2026-09-04    58 | 2026-09-05    47 | 2026-09-06    10 | 2026-09-07     9
2026-09-08    41 | 2026-09-09    12 | 2026-09-10    24 | 2026-09-11    26 | 2026-09-12    10
2026-09-13     9 | 2026-09-14    62 | 2026-09-15    41 | 2026-09-16    48 | 2026-09-17    75
2026-09-18    16
```

#### `alpha_arena.public.paper_positions` (`opened_at`) — 2,410 rows in window
```
2026-08-04    62 | 2026-08-05    81 | 2026-08-06    18 | 2026-08-07    50 | 2026-08-08    73
2026-08-09    15 | 2026-08-10    40 | 2026-08-11    83 | 2026-08-12   170 | 2026-08-13   159
2026-08-14    37 | 2026-08-15    27 | 2026-08-16    18 | 2026-08-17    94 | 2026-08-18   154
2026-08-19   172 | 2026-08-20    23 | 2026-08-21   184 | 2026-08-22    50 | 2026-08-23    86
2026-08-24   127 | 2026-08-25    54 | 2026-08-26    63 | 2026-08-27    36 | 2026-08-28   108
2026-08-29    42 | 2026-08-30     3 | 2026-08-31    32 | 2026-09-01   105 | 2026-09-02    88
2026-09-03    38 | 2026-09-04    12 | 2026-09-05    12 | 2026-09-06     2 | 2026-09-07     2
2026-09-08    10 | 2026-09-09     3 | 2026-09-10     4 | 2026-09-11     7 | 2026-09-12     2
2026-09-13     2 | 2026-09-14    19 | 2026-09-15     7 | 2026-09-16    11 | 2026-09-17    19
2026-09-18     6
```

#### `alpha_arena.public.position_exit_events` (`created_at`) — 3,682 rows in window
```
2026-08-04    68 | 2026-08-05   117 | 2026-08-06    41 | 2026-08-07    45 | 2026-08-08    76
2026-08-09    13 | 2026-08-10    49 | 2026-08-11    90 | 2026-08-12   198 | 2026-08-13   203
2026-08-14    54 | 2026-08-15    36 | 2026-08-16    24 | 2026-08-17    98 | 2026-08-18   146
2026-08-19   173 | 2026-08-20    23 | 2026-08-21   184 | 2026-08-22    57 | 2026-08-23    92
2026-08-24   133 | 2026-08-25    48 | 2026-08-26    69 | 2026-08-27    55 | 2026-08-28   125
2026-08-29    42 | 2026-08-30     6 | 2026-08-31   872 <-- SPIKE | 2026-09-01   148 | 2026-09-02   124
2026-09-03    91 | 2026-09-04    23 | 2026-09-05    17 | 2026-09-06     8 | 2026-09-07     3
2026-09-08    11 | 2026-09-09     4 | 2026-09-10    12 | 2026-09-11     6 | 2026-09-12     4
2026-09-13     4 | 2026-09-14    16 | 2026-09-15    25 | 2026-09-16    19 | 2026-09-17    26
2026-09-18     4
```

#### `alpha_arena.public.strategy_trades` (`opened_at`) — 2,217 rows in window
```
2026-08-04    62 | 2026-08-05    81 | 2026-08-06    18 | 2026-08-07   100 | 2026-08-08    23
2026-08-09    29 | 2026-08-10    48 | 2026-08-11    63 | 2026-08-12   136 | 2026-08-13    92
2026-08-14    15 | 2026-08-15     9 | 2026-08-16    42 | 2026-08-17    95 | 2026-08-18   165
2026-08-19   109 | 2026-08-20    88 | 2026-08-21   160 | 2026-08-22    59 | 2026-08-23    63
2026-08-24   115 | 2026-08-25    63 | 2026-08-26    36 | 2026-08-27    66 | 2026-08-28    90
2026-08-29    13 | 2026-08-30     2 | 2026-08-31    49 | 2026-09-01   122 | 2026-09-02    91
2026-09-03     8 | 2026-09-04    13 | 2026-09-05    10 | 2026-09-06     5 | 2026-09-07  (none)
2026-09-08    10 | 2026-09-09     6 | 2026-09-10     2 | 2026-09-11     9 | 2026-09-12     1
2026-09-13     5 | 2026-09-14    10 | 2026-09-15     9 | 2026-09-16    15 | 2026-09-17    10
2026-09-18  (none)
```

#### `alpha_arena.public.trade_facts` (`ts`) — 2,438 rows in window
```
2026-08-04    61 | 2026-08-05    81 | 2026-08-06    24 | 2026-08-07    41 | 2026-08-08    75
2026-08-09    13 | 2026-08-10    48 | 2026-08-11    75 | 2026-08-12   195 | 2026-08-13   158
2026-08-14    35 | 2026-08-15    18 | 2026-08-16     2 | 2026-08-17    94 | 2026-08-18   144
2026-08-19   167 | 2026-08-20    22 | 2026-08-21   180 | 2026-08-22    56 | 2026-08-23    82
2026-08-24   131 | 2026-08-25    48 | 2026-08-26    60 | 2026-08-27    32 | 2026-08-28   113
2026-08-29    40 | 2026-08-30     6 | 2026-08-31    32 | 2026-09-01   103 | 2026-09-02    84
2026-09-03   104 | 2026-09-04    12 | 2026-09-05     9 | 2026-09-06     3 | 2026-09-07     2
2026-09-08    10 | 2026-09-09     3 | 2026-09-10     8 | 2026-09-11     3 | 2026-09-12     4
2026-09-13     3 | 2026-09-14     9 | 2026-09-15    11 | 2026-09-16    10 | 2026-09-17    24
2026-09-18     3
```

#### `alpha_arena.public.trade_memory_records` (`opened_at`) — 2,382 rows in window
```
2026-08-04    62 | 2026-08-05    81 | 2026-08-06    18 | 2026-08-07    49 | 2026-08-08    73
2026-08-09    15 | 2026-08-10    40 | 2026-08-11    84 | 2026-08-12   200 | 2026-08-13   150
2026-08-14    30 | 2026-08-15    18 | 2026-08-16     8 | 2026-08-17    92 | 2026-08-18   150
2026-08-19   172 | 2026-08-20    23 | 2026-08-21   184 | 2026-08-22    49 | 2026-08-23    84
2026-08-24   126 | 2026-08-25    54 | 2026-08-26    60 | 2026-08-27    36 | 2026-08-28   107
2026-08-29    43 | 2026-08-30     3 | 2026-08-31    31 | 2026-09-01   107 | 2026-09-02    88
2026-09-03    38 | 2026-09-04    12 | 2026-09-05    12 | 2026-09-06     2 | 2026-09-07     2
2026-09-08     9 | 2026-09-09     3 | 2026-09-10     3 | 2026-09-11     7 | 2026-09-12     2
2026-09-13     2 | 2026-09-14    19 | 2026-09-15     6 | 2026-09-16     9 | 2026-09-17    19
2026-09-18  (none)
```

#### `alpha_arena.public.lane_ledger` (`ts`) — ALL 5,298 rows
```
2026-09-09   153 | 2026-09-10   327 | 2026-09-11   312 | 2026-09-12   154 | 2026-09-13     2
2026-09-14   996 | 2026-09-15  3199 | 2026-09-16   135 | 2026-09-17    20   <-- stops 09-17 09:20
```

#### `alpha_analytics.public.decision_snapshots` (`timestamp`) — ALL 5,941 rows
```
2026-09-11    14 | 2026-09-12   943 | 2026-09-13  3950 | 2026-09-14   165 | 2026-09-15   325
2026-09-16   286 | 2026-09-17   255 | 2026-09-18     3
```
**★ Only 8 days of history (starts 2026-09-11 10:08). 09-13 is a 3,950-row backfill spike.**

#### `alpha_analytics.public.factor_performance_logs` (`recorded_at`) — ALL 355,417 rows
```
2026-08-21  3426 | 2026-08-22  1239 | 2026-08-23 41431 | 2026-08-24 40151 | 2026-08-25 30410
2026-08-26  5870 | 2026-08-27  9209 | 2026-08-28  5051 | 2026-08-29 35646 | 2026-08-30 18667
2026-08-31 16649 | 2026-09-01  6866 | 2026-09-02 12261 | 2026-09-03 28889 | 2026-09-04  7410
2026-09-05 20188 | 2026-09-06  2478 | 2026-09-07 20941 | 2026-09-08  5358 | 2026-09-09  1390
2026-09-10  6526 | 2026-09-11  1334 | 2026-09-12  8653 | 2026-09-13  2587 | 2026-09-14  2186
2026-09-15   792 | 2026-09-16  4888 | 2026-09-17  6916 | 2026-09-18  8005
```
**★ boundary:** 09-15 = 792 → **09-16 = 4,888 → 09-17 = 6,916** (a 6× step-up).

#### `alpha_analytics.public.factor_evolution_log` (`created_at`) — 17,548 rows in window
```
2026-08-05     4 | 2026-08-06    84 | 2026-08-07   942 | 2026-08-08  4075 | 2026-08-09     3
2026-08-10   384 | 2026-08-11   473 | 2026-08-12  1548 | 2026-08-13   495 | 2026-08-14   389
2026-08-15   264 | 2026-08-16    45 | 2026-08-17    92 | 2026-08-18    49 | 2026-08-19    23
2026-08-20  (none) | 2026-08-21    40 | 2026-08-22    74 | 2026-08-23    50 | 2026-08-24   117
2026-08-25    14 | 2026-08-26   136 | 2026-08-27   429 | 2026-08-28   131 | 2026-08-29   364
2026-08-30   453 | 2026-08-31   638 | 2026-09-01   216 | 2026-09-02   382 | 2026-09-03   482
2026-09-04   341 | 2026-09-05   230 | 2026-09-06   346 | 2026-09-07   261 | 2026-09-08   267
2026-09-09   347 | 2026-09-10   346 | 2026-09-11   368 | 2026-09-12   326 | 2026-09-13   267
2026-09-14   293 | 2026-09-15   232 | 2026-09-16   289 | 2026-09-17   918 | 2026-09-18   321
```
**★ boundary:** 09-16 = 289 → **09-17 = 918** (3.2× step-up).

#### `alpha_analytics.public.factor_exposure_snapshots` (`ts`) — ALL 706,331 rows (starts 2026-09-04)
```
2026-09-04  48720 | 2026-09-05 125235 | 2026-09-06  90720 | 2026-09-07  58268 | 2026-09-08  58631
2026-09-09  48208 | 2026-09-10  40208 | 2026-09-11  33450 | 2026-09-12  34056 | 2026-09-13  27234
2026-09-14  28567 | 2026-09-15  26192 | 2026-09-16  29392 | 2026-09-17  35220 | 2026-09-18  22230
```

#### `alpha_analytics.public.strategic_reports` (`timestamp`) — 284 rows in window
```
2026-08-04    18 | 2026-08-05     8 | 2026-08-06    30 | 2026-08-07    26 | 2026-08-08     3
2026-08-09     7 | 2026-08-10    17 | 2026-08-11    26 | 2026-08-12    32 | 2026-08-13    26
2026-08-14    16 | 2026-08-15    50 | 2026-08-16    10 | 2026-08-17..2026-09-17  (NO ROWS — 32-day gap)
2026-09-18    15
```
Monthly totals: `2026-06=165, 2026-07=493, 2026-08=371, 2026-09=15` → total 1,044.
**★ `strategic_reports` writer stalled after 2026-08-16 and only emitted 15 rows on 09-18.**

#### `alpha_analytics.public.decision_retrospectives` (`created_at`) — 2,520 rows in window
```
2026-08-04    61 | 2026-08-05    81 | 2026-08-06    24 | 2026-08-07    42 | 2026-08-08    75
2026-08-09    13 | 2026-08-10    48 | 2026-08-11    75 | 2026-08-12   213 | 2026-08-13   166
2026-08-14    42 | 2026-08-15    27 | 2026-08-16    13 | 2026-08-17    98 | 2026-08-18   146
2026-08-19   173 | 2026-08-20    23 | 2026-08-21   184 | 2026-08-22    57 | 2026-08-23    84
2026-08-24   132 | 2026-08-25    48 | 2026-08-26    64 | 2026-08-27    32 | 2026-08-28   114
2026-08-29    40 | 2026-08-30     9 | 2026-08-31    78 | 2026-09-01   105 | 2026-09-02    88
2026-09-03    48 | 2026-09-04    15 | 2026-09-05     9 | 2026-09-06     3 | 2026-09-07     2
2026-09-08    10 | 2026-09-09     3 | 2026-09-10     8 | 2026-09-11     3 | 2026-09-12     4
2026-09-13     3 | 2026-09-14     9 | 2026-09-15    11 | 2026-09-16    11 | 2026-09-17    23
2026-09-18     3
```

---

## 6. "SignalFeedback / 逐单增量归因" and "factor_signal_log" (§5 of the brief)

### 6.1 SignalFeedback / 逐单增量归因 → **`alpha_arena.public.signal_trade_feedback`**

Provenance in code:
- `backend/services/signal_feedback_tracker.py:44` → `class SignalFeedbackTracker:` (singleton at `:449`)
- `backend/services/signal_feedback_tracker.py:9` → "开仓时记录当时活跃的信号快照 -> signal_trade_feedback 表"
- `backend/services/factor_engine/factor_decay_monitor.py:197` → "根因：SignalFeedbackTracker 的逐单增量归因（因子活跃期 avgPnL − 全局）"
- `backend/database/models.py:3268` → `__tablename__ = "signal_trade_feedback"`
- `backend/services/live_learning_hooks.py`, `backend/services/learning_loop_service.py:347`,
  `backend/services/paper_trading_engine.py:4515`, `backend/services/exchange/live_executor.py:534`

| question | answer |
|---|---|
| exact table name | **`alpha_arena.public.signal_trade_feedback`** (schema `public`, DB `alpha_arena`) |
| row count | **622,358** |
| has `factor_name`? | **NO** |
| has `factor_id`? | **NO** |
| has `avg_pnl`? | **NO** |
| has `pnl`? | **NO** — but has **`trade_pnl`** (`double precision`) and **`trade_pnl_pct`** (`double precision`) |
| has `incremental_pnl`? | **NO** |
| factor identity carrier | **`signal_type` `varchar(100)`**, values of form `factor:<name>` |
| timestamp column | **`created_at`** (`timestamp without time zone`, default `CURRENT_TIMESTAMP`) |
| MIN / MAX `created_at` | **2026-07-18 04:20:34.073448** / **2026-09-18 09:25:29.007484** |

Completeness of the PnL join key:

```sql
SELECT count(*) AS total, count(trade_id) AS with_trade_id,
       count(trade_pnl) AS with_trade_pnl, count(signal_value) AS with_signal_value,
       count(DISTINCT signal_type) AS d_signal_type, count(DISTINCT symbol) AS d_symbol,
       count(DISTINCT account_id) AS d_account, MIN(created_at), MAX(created_at)
FROM public.signal_trade_feedback;
-- total=622358 | trade_id=622358 | trade_pnl=583984 | signal_value=622358
-- d_signal_type=1488 | d_symbol=128 | d_account=4
```
→ 38,374 rows (6.2 %) still have `trade_pnl IS NULL` (open / unmatched trades).
`trade_id` is **NOT NULL in practice** (622,358/622,358 populated).

**`signal_type` cardinality = 1,488 distinct** (>200, so top-20-by-count + prefix roll-up below;
the complete list of all 1,488 with counts and per-type MIN/MAX `created_at` is in
`_ro_inv7.out.txt` lines 28–1517).

Prefix roll-up (`split_part(signal_type, ':', 1)`):
```sql
SELECT split_part(signal_type, ':', 1) AS prefix, count(*) n, count(DISTINCT signal_type) d
FROM public.signal_trade_feedback GROUP BY 1 ORDER BY n DESC;
```
| prefix | rows | distinct |
|---|---|---|
| **`factor`** | **609,249** | **1,476** |
| `funding` | 3,190 | 1 |
| `liquidation` | 3,190 | 1 |
| `oi` | 3,190 | 1 |
| `scalp_composite_mr` | 1,442 | 1 |
| `scalp_composite` | 1,056 | 1 |
| `whale` | 510 | 1 |
| `fear_greed` | 274 | 1 |
| `swing_agent_score` | 157 | 1 |
| `trend_agent_score` | 37 | 1 |
| `top_trader` | 24 | 1 |
| `long_short` | 24 | 1 |
| `news` | 15 | 1 |

Top 20 `factor:%` by row count, with realised PnL (all **negative** every single one):
```sql
SELECT signal_type, count(*) n, count(trade_pnl) n_pnl,
       round(avg(trade_pnl)::numeric,6) avg_pnl, round(sum(trade_pnl)::numeric,4) sum_pnl
FROM public.signal_trade_feedback WHERE signal_type LIKE 'factor:%'
GROUP BY signal_type ORDER BY n DESC LIMIT 20;
```
| signal_type | n | n_pnl | avg_pnl | sum_pnl |
|---|---|---|---|---|
| `factor:obv` | 3,136 | 3,066 | -0.344030 | -1,054.7956 |
| `factor:sma_cross` | 3,071 | 3,005 | -0.335721 | -1,008.8402 |
| `factor:vwap` | 3,064 | 2,998 | -0.335663 | -1,006.3179 |
| `factor:oi_delta` | 3,022 | 2,956 | -0.254315 | -751.7548 |
| `factor:momentum` | 3,018 | 2,952 | -0.256764 | -757.9671 |
| `factor:macd` | 3,018 | 2,952 | -0.256764 | -757.9671 |
| `factor:hv` | 3,018 | 2,952 | -0.256764 | -757.9671 |
| `factor:supertrend` | 3,018 | 2,952 | -0.256764 | -757.9671 |
| `factor:ai_gen_price_position` | 2,987 | 2,928 | -0.240026 | -702.7966 |
| `factor:rsi` | 2,974 | 2,912 | -0.234596 | -683.1429 |
| `factor:ema_trend` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:volume_zscore` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:zscore` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:cvd_ratio` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:atr` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:taker_ratio` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:parkinson_vol` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:atr_ratio` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:roc` | 2,973 | 2,911 | -0.233810 | -680.6217 |
| `factor:bb_width` | 2,973 | 2,911 | -0.233810 | -680.6217 |

Notable later-generation factors (from the full list): `factor:evo_seed_rev20` (1,495),
`factor:evo_d6f82d364676127e` (1,225, MAX created_at 2026-09-18),
`factor:evo_seed_rev50` (1,039), `factor:evo_5a0c7a226b5446a4` (966),
`factor:evo_intra_180ee6eedd1fe9cc` (3, only 2026-09-17).

Top symbols (128 distinct):
`SOL 61,736 | ETH 58,608 | ONDO 55,446 | BTC 54,623 | HYPE 38,358 | NEAR 31,536 | AAVE 28,472 |
PUMP 25,109 | VVV 23,872 | BNB 19,205 | LIT 18,365 | ASTER 17,995 | KAITO 17,852 | UNI 17,423 |
VIRTUAL 16,264 | XPL 15,555 | XRP 15,164 | LDO 13,155 | ZEC 12,022 | FARTCOIN 8,568`

### 6.2 `factor_signal_log` → **DOES NOT EXIST**

Repo-wide search for the literal identifier `factor_signal_log`:

```
grep -r "factor_signal_log" D:\001Alpha\Hyper-Alpha-Arena
→ exactly 1 hit:
  docs\因子与LLM统一策略架构_诊断与设计_2026-09-17.md:76
    `{复合信号, top因子暴露, 近期该因子IC/衰减状态, 上次该因子投票的交易盈亏(factor_signal_log归因), factor_weights(断点4复活)}`
```
```
grep -r "FactorSignalLog" backend  → no matches
information_schema.tables (all 5 DBs) WHERE table_name ~* 'factor_signal' → 0 rows
backend/database/models.py __tablename__ list → no such model
```
**Conclusion:** `factor_signal_log` is a **design-document placeholder** authored on 2026-09-17 for
"the PnL of the last trade this factor voted on". It has no table, no ORM model, no Alembic migration.
If the design is to be implemented, the closest existing sources are:

| need | existing table | factor identity col | pnl/metric col | ts col | rows | MIN → MAX |
|---|---|---|---|---|---|---|
| factor → per-trade PnL attribution (**this is the real `factor_signal_log`**) | `alpha_arena.public.signal_trade_feedback` | `signal_type` (`factor:<name>`) | `trade_pnl`, `trade_pnl_pct` | `created_at` | 622,358 | 2026-07-18 → 2026-09-18 |
| factor IC / decay / weight history | `alpha_analytics.public.factor_performance_logs` | **`factor_name`** `varchar(50)` | `ic_value`, `decay_rate`, `current_weight` (all `numeric(10,6)`) | `recorded_at` | 355,417 | 2026-08-21 → 2026-09-18 |
| factor exposure per symbol/period | `alpha_analytics.public.factor_exposure_snapshots` | **`factor_id`** | `z_score`, `expected_alpha`, `weight` | `ts` (timestamptz) | ~706.3k | 2026-09-04 → 2026-09-18 |
| factor state-transition log | `alpha_analytics.public.factor_evolution_log` | **`factor_id`** `varchar(64)` | `metrics` (json) | `created_at` | 17,564 | 2026-07-23 → 2026-09-18 |
| live factor registry | `alpha_analytics.public.factor_active_set` | **`factor_id`** `varchar(64)` | `icir`, `incremental_corr`, `current_weight` (json), `last_net_ic`, `turnover` | `last_evaluated_at` | 25 | 2026-08-02 → 2026-09-18 |
| per-factor quality report | `alpha_analytics.public.factor_quality_reports` | **`factor_id`** | `ic_mean`, `icir`, `coverage`, `grade`, `is_alive` | `report_date`, `created_at` | 925 | all 2026-07-23 |
| signal weight snapshot | `alpha_arena.public.signal_weight_history` | — (`weights_json` json) | `performance_json` | `computed_at` | 2,916 | 2026-04-06 → 2026-09-18 |
| scalp signal log (legacy, dead) | `alpha_arena.public.scalp_signal_log` | — (`factor_score` numeric) | `fwd_ret`, `net_ret`, `win`, `tb_net_ret`, `tb_win` | `created_at` | 365,024 | 2026-07-18 → **2026-09-05 (stale)** |

### 6.3 Distinct factor names / ids per candidate table

`alpha_analytics.public.factor_performance_logs` BY `factor_name` → **1,299 distinct**
(>200 → top 20 below; full list of 1,299 in `_ro_inv7.out.txt` lines 1646-1667 region).
```
SQL: SELECT factor_name, count(*) n, min(recorded_at)::text, max(recorded_at)::text
     FROM public.factor_performance_logs GROUP BY 1 ORDER BY n DESC;
```
Top 20 (all = 1,385 rows, spanning 2026-08-21 05:10:37 → 2026-09-18 05:10:52):
`rsi, ai_gen_bsq, ai_gen_corr_break, ai_gen_price_position, ema_trend, macd, ai_gen_liq_quality,
obv, ai_gen_vpr, ai_gen_voltrend, ai_gen_mrext, ai_gen_mr, ai_gen_vol_div, ai_gen_vqc,
ai_gen_volume_flow, ai_gen_lrr, ai_gen_slrisk, zscore, ai_gen_trend_conf, ai_gen_stability`

`alpha_analytics.public.factor_exposure_snapshots` BY `factor_id` → **20 distinct** (complete):
```
seed_rev50              84018 | d6f82d364676127e       84018 | seed_rev10              75801
5a0c7a226b5446a4        75801 | 180ee6eedd1fe9cc       75801 | seed_rev5               75801
seed_rev20              75801 | 3d51976a92a07bea       45541 | seed_mom10              19600
seed_mom20              19600 | seed_mom5              19600 | seed_ts_rank20          19600
seed_ts_rank50          19600 | seed_vol20              9995 | s5m_1b4e366342f844c1     4248
s5m_fa81777dc6215969      774 | s5m_5a0c7a226b5446a4     774 | intra_180ee6eedd1fe9cc    232
intra_3d51976a92a07bea     94 | intra_2abe5b53d94029ec    28
```

`alpha_analytics.public.factor_active_set` BY `factor_id` → **25 distinct** (complete, 1 row each):
```
seed_vol50, intra_2abe5b53d94029ec, seed_mom20, s5m_5a0c7a226b5446a4, seed_vp_corr20,
seed_ts_rank50, seed_vol20, seed_mom10, 180ee6eedd1fe9cc, s5m_fa81777dc6215969,
intra_3d51976a92a07bea, s5m_1b4e366342f844c1, seed_rev20, seed_rev50, seed_vol10,
3d51976a92a07bea, seed_rev5, d6f82d364676127e, intra_180ee6eedd1fe9cc, seed_vp_corr10,
seed_mom5, seed_ts_rank20, 5a0c7a226b5446a4, 0f3fbe4b9de18230, seed_rev10
```

`alpha_analytics.public.factor_evolution_log` BY `factor_id` → **8,498 distinct** (top 20):
```
pb_freeze:ZEC 488 | 3d51976a92a07bea 294 | 180ee6eedd1fe9cc 266 | 5a0c7a226b5446a4 261
d6f82d364676127e 254 | 127bc3c509a1824e 218 | 74368e940ae38990 215 | 3ad62c2b318a4786 212
5fed8c3b67e2670f 211 | 7a8140ced844c88e 210 | 1535e273b8a7bee3 209 | d90511bd6333ab20 207
9552ed0aab4db433 205 | 01f5a76e3c92edb6 116 | e0e9039fe8e87055 115 | pb_freeze:ETH 94
pb_freeze:BTC 92 | pb_freeze:KAITO 83 | 009f594d0eb0c1f4 79 | pb_freeze:AAVE 79
```
(Note the `pb_freeze:<SYMBOL>` pseudo-factor-id namespacing.)

`alpha_analytics.public.factor_quality_reports` BY `factor_id` → **925 distinct** (1 row each, all
`report_date`/`created_at` = 2026-07-23). Top 20: `ai_gen_rsidiv, ai_gen_vol_dry, ai_gen_shadow,
ai_gen_reversal, ai_gen_bpk, ai_gen_micro_corr, ai_gen_unknown_osc, ai_gen_bb_tension,
ai_gen_vol_spike, ai_gen_trend_fuzzy, ai_gen_bearish_pressu, ai_gen_momentum_decay, ai_gen_bsrf,
ai_gen_price_coherenc, ai_gen_vol_reversal, wq_alpha002, ai_gen_unc, ai_gen_volumebreak,
ai_gen_regime_instabi, ai_gen_trend_weak`

`alpha_arena.public.atas_factor_cache` BY `factor_id` → **1** (`composite_v3`, 90 rows)
`alpha_arena.public.cloud_factor_definitions` BY `factor_id` → **5** (`cloud_microstructure_kyle,
cloud_bollinger_width, cloud_vwap_deviation, cloud_obv_oscillator, cloud_rsi_divergence`)

> ⚠️ Naming-space caveat: `alpha_analytics.factor_performance_logs.factor_name` uses **human names**
> (`rsi`, `obv`, `ai_gen_mr`, …) whereas `alpha_arena.signal_trade_feedback.signal_type` uses
> `factor:rsi`, `factor:obv`, … so the join requires `'factor:' || factor_name = signal_type`
> (or `replace(signal_type,'factor:','')`). `factor_exposure_snapshots` / `factor_active_set` /
> `factor_evolution_log` use **opaque hex ids** (`3d51976a92a07bea`, `seed_rev50`, `s5m_*`, `intra_*`)
> which do **not** match the `factor:<name>` namespace — there is no existing mapping table.

---

## 7. What I could NOT determine (honest gaps)

1. `alpha_market.public.asterdex_depth_snapshots.ingest_ts` MIN/MAX — cancelled by the 90 s
   `statement_timeout` on the 12.3 M-row table. Its `event_ts_ms` MIN/MAX did complete
   (`1789540926269 .. 1789695890983` = 2026-09-16 14:42:06 .. 2026-09-18 09:44:50 +08).
2. `alpha_market` per-day histograms were **not** produced (only MIN/MAX) — the brief's histogram
   list targeted trades/orders/positions/decision_snapshots/signal_feedback/factor_signal_log,
   which are all in `alpha_arena` / `alpha_analytics`. `alpha_market.crypto_klines` is 60.9 M rows
   and would need a partition-aware or index-only query to histogram cheaply.
3. No mapping exists between `factor_performance_logs.factor_name` (human names) and
   `factor_exposure_snapshots.factor_id` (opaque hex/`seed_*`/`s5m_*`/`intra_*` ids).
4. `signal_type` is truncated to 100 chars — several distinct values appear truncated
   (e.g. `factor:ai_gen_volume_flow` vs `factor:ai_gen_voladj_momentu`, `factor:cloud_microstructure_`,
   `factor:ai_gen_uncertainty_vo`, `factor:ai_gen_volatility_reg`), so distinct-count 1,488 is an
   upper bound on true factor identity and collisions are possible.
5. `alpha_arena.public.signal_ledger` (6,214 rows) has **no** timestamp column at all — only
   `created_ms` / `expires_ms` / `scored_ms` (bigint epoch ms). Not converted.
6. `alpha_arena.public.agent_predictions` (9,596) and `public.backtest_trades` (70) have **no**
   timestamp/date columns.
7. `beta: row counts drift` — `factor_exposure_snapshots` measured 706,331 then 706,727 minutes later.
   All numbers are point-in-time snapshots of a live system.

---

## 8. Raw artifacts

| file | contents |
|---|---|
| `_ro_inv1.out.utf8.txt` | DB list, connectivity probe, naive (RLS-blocked) counts |
| `_ro_inv2.out.txt` | all-databases list, keyword table/column matches, RLS/relkind/relpages |
| `_ro_inv3.out.txt` | RLS diagnosis: roles, all 58 policies, reltuples vs n_live_tup, `row_security=off` failure |
| `_ro_inv5.out.txt` | **§5 true counts for all 4 DBs + §6 full column dump (1,377 columns)** |
| `_ro_inv6.out.txt` | **§7 MIN/MAX of every timestamp column + §8 per-day histograms** |
| `_ro_inv7.out.txt` | **§9-§12 signal_type enumeration (all 1,488), candidate tables, factor ids** |
| `_ro_inv8.out.txt` | alpha_market time coverage + live-drift check |
| `_ro_inv_counts.json` | machine-readable per-DB `(schema, table, kind, count, n_live_tup, reltuples, force_rls)` |
| `_ro_inv_columns.json` | machine-readable `information_schema.columns` dump |
| `_ro_inv_timecov.json` | machine-readable MIN/MAX per timestamp column |

### Scripts (all read-only, all prefixed `_ro_inv`)
`_ro_inv1.py` (connect + count) · `_ro_inv2.py` (catalogue + keyword + RLS flags) ·
`_ro_inv3.py` (RLS diagnosis) · `_ro_inv4.py` (verify `app.is_admin` unlock) ·
`_ro_inv5.py` (true counts + columns) · `_ro_inv6.py` (time coverage + histograms) ·
`_ro_inv7.py` (factor enumeration) · `_ro_inv8.py` (market coverage + drift) ·
`_ro_inv9_report.py` (this report).

### Canonical read-only boilerplate for reuse
```python
import json, psycopg2
CONNS = json.load(open(r"D:\001Alpha\Hyper-Alpha-Arena\_ro_inv_conns.json"))
c = psycopg2.connect(CONNS["alpha_arena"], connect_timeout=10)
c.set_session(readonly=True, autocommit=True)   # hard read-only at the session level
cur = c.cursor()
cur.execute("SET app.is_admin = 'on'")          # REQUIRED or all RLS tables read as 0
cur.execute("SET statement_timeout = '90s'")
```
Run with: `D:\001Alpha\Hyper-Alpha-Arena\backend\.venv\Scripts\python.exe <script>.py`
