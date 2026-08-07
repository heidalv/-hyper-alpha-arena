#!/usr/bin/env python3
"""
SQLite → PostgreSQL 数据迁移工具

用法:
    python migrate_to_postgresql.py [--sqlite-url SQLITE_URL] [--pg-url PG_URL] [--batch-size 5000] [--dry-run]

功能:
    - 自动检测所有表并迁移
    - 分批流式迁移（默认 5000 行/批）
    - 断点续传（进度记录到 .migration_progress.json）
    - 参数化查询（无 SQL 注入风险）
    - 迁移后数据校验（行数对比）
    - 支持 --dry-run 模式预检
    - 每批独立事务，单行失败不影响整批
"""

import sys
import os
import json
import time
import argparse
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")

PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "..", ".migration_progress.json")


def get_sqlite_tables(sqlite_eng) -> list:
    """获取 SQLite 中所有用户表名。"""
    from sqlalchemy import text
    with sqlite_eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )).fetchall()
        return [r[0] for r in rows]


def get_pg_tables(pg_eng) -> list:
    """获取 PostgreSQL 中所有用户表名。"""
    from sqlalchemy import text
    with pg_eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )).fetchall()
        return [r[0] for r in rows]


def get_table_columns(eng, table_name: str) -> list:
    """获取表的列名列表。"""
    from sqlalchemy import inspect
    inspector = inspect(eng)
    columns = inspector.get_columns(table_name)
    return [c["name"] for c in columns]


def get_boolean_columns(eng, table_name: str) -> set:
    """获取 PG 表中 BOOLEAN 类型的列名集合。"""
    from sqlalchemy import inspect
    inspector = inspect(eng)
    columns = inspector.get_columns(table_name)
    bool_cols = set()
    for c in columns:
        type_str = str(c.get("type", "")).upper()
        if "BOOL" in type_str:
            bool_cols.add(c["name"])
    return bool_cols


def get_row_count(eng, table_name: str) -> int:
    """获取表行数。"""
    from sqlalchemy import text
    with eng.connect() as conn:
        result = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
        return result.fetchone()[0]


def load_progress() -> dict:
    """加载迁移进度。"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_progress(progress: dict):
    """保存迁移进度。"""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def migrate_table(sqlite_eng, pg_eng, table_name: str, batch_size: int, dry_run: bool, progress: dict):
    """分批迁移单张表 — 每批独立事务，单行失败不影响其他行。"""
    from sqlalchemy import text

    # 获取列名交集（两库都有的列）
    sqlite_cols = get_table_columns(sqlite_eng, table_name)
    pg_cols = get_table_columns(pg_eng, table_name)
    common_cols = [c for c in sqlite_cols if c in pg_cols]

    if not common_cols:
        logger.warning(f"  {table_name}: 无公共列，跳过")
        return

    cols_str = ", ".join(f'"{c}"' for c in common_cols)
    params_str = ", ".join(f":{c}" for c in common_cols)

    # 检查已迁移行数
    pg_count = get_row_count(pg_eng, table_name) if not dry_run else 0
    offset = progress.get(table_name, {}).get("offset", 0)

    # 如果 PG 已有数据且无断点记录，跳过
    if pg_count > 0 and offset == 0:
        logger.info(f"  {table_name}: PG 已有 {pg_count} 行，跳过")
        return

    sqlite_count = get_row_count(sqlite_eng, table_name)
    if sqlite_count == 0:
        logger.info(f"  {table_name}: SQLite 无数据，跳过")
        return

    if dry_run:
        logger.info(f"  {table_name}: 需迁移 {sqlite_count} 行, {len(common_cols)} 列")
        return

    # 简单 INSERT（不用 ON CONFLICT，避免静默失败）
    insert_sql = text(
        f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({params_str})'
    )

    # 检测 PG 中的 BOOLEAN 列（SQLite 用 0/1 存储）
    pg_bool_cols = get_boolean_columns(pg_eng, table_name)

    migrated = offset
    failed = 0

    while migrated < sqlite_count:
        # 从 SQLite 分批读取（使用独立连接）
        batch_sql = text(
            f'SELECT {cols_str} FROM "{table_name}" '
            f'LIMIT :limit OFFSET :offset'
        )

        with sqlite_eng.connect() as sqlite_conn:
            rows = sqlite_conn.execute(
                batch_sql, {"limit": batch_size, "offset": migrated}
            ).fetchall()

        if not rows:
            break

        # 每批独立事务 — 单行失败回滚该行但不影响批内其他行
        batch_inserted = 0
        for row in rows:
            params = {}
            for i, col in enumerate(common_cols):
                val = row[i]
                # SQLite 0/1 → PG BOOLEAN 转换
                if col in pg_bool_cols and val is not None:
                    params[col] = bool(val)
                else:
                    params[col] = val
            try:
                with pg_eng.begin() as pg_conn:
                    pg_conn.execute(insert_sql, params)
                    batch_inserted += 1
            except Exception as e:
                failed += 1
                if failed <= 5:
                    logger.warning(f"  {table_name}: 行插入失败: {e}")
                elif failed == 6:
                    logger.warning(f"  {table_name}: 后续失败不再显示...")

        migrated += len(rows)
        progress[table_name] = {"offset": migrated, "total": sqlite_count}
        save_progress(progress)

        pct = min(100, migrated * 100 // sqlite_count)
        logger.info(f"  {table_name}: {migrated}/{sqlite_count} ({pct}%) 插入={batch_inserted}/{len(rows)}")

    status = f"完成, 处理 {migrated - offset} 行"
    if failed > 0:
        status += f", 失败 {failed} 行"
    logger.info(f"  {table_name}: {status}")


def main():
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 迁移工具")
    parser.add_argument("--sqlite-url", default=None,
                        help="SQLite 连接串 (默认读取当前 .env)")
    parser.add_argument("--pg-url", default=None,
                        help="PostgreSQL 连接串 (默认读取当前 .env)")
    parser.add_argument("--batch-size", type=int, default=5000,
                        help="每批迁移行数 (默认 5000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅检查不实际迁移")
    parser.add_argument("--reset", action="store_true",
                        help="清除断点进度，从头开始")
    args = parser.parse_args()

    from sqlalchemy import create_engine

    # 确定连接串
    sqlite_url = args.sqlite_url or os.environ.get(
        "SQLITE_SOURCE_URL",
        os.environ.get("DATABASE_URL", "sqlite:///./data/alpha_arena.db")
    )
    pg_url = args.pg_url or os.environ.get(
        "PG_TARGET_URL",
        os.environ.get("DATABASE_URL", "").startswith("postgresql") and os.environ.get("DATABASE_URL")
        or "postgresql://alpha_user:alpha_pass@localhost:5432/alpha_arena"
    )

    if not pg_url or not pg_url.startswith("postgresql"):
        logger.error("PostgreSQL URL 无效。设置 PG_TARGET_URL 或使用 --pg-url")
        sys.exit(1)

    logger.info(f"SQLite: {sqlite_url}")
    logger.info(f"PostgreSQL: {pg_url}")
    logger.info(f"Batch size: {args.batch_size}")
    logger.info(f"Dry run: {args.dry_run}")

    sqlite_eng = create_engine(sqlite_url)
    pg_eng = create_engine(pg_url)

    # 获取表列表
    sqlite_tables = get_sqlite_tables(sqlite_eng)
    pg_tables = get_pg_tables(pg_eng)
    logger.info(f"SQLite 表: {len(sqlite_tables)} 个, PG 表: {len(pg_tables)} 个")

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        logger.info("断点进度已清除")

    progress = load_progress()

    # 迁移顺序：FK 依赖拓扑排序，父表在前，子表在后
    # 参考 models.py 中所有 ForeignKey 关系
    priority_order = [
        # Level 0: 无 FK 依赖的独立表
        "users",                       # 被 accounts, user_auth_sessions 等引用
        "llm_configurations",          # 被 accounts.llm_config_id 引用
        "prompt_templates",            # 被 ai_strategies, account_prompt_bindings 等引用
        "signal_pools",                # 被 signal_performance_history 引用
        "dingtalk_bots",               # 被 dingtalk_notifications 引用
        "backtest_runs",               # 被 backtest_trades 引用
        "rebate_positions",            # 被 rebate_orders 引用
        "system_configs",              # 无 FK 引用

        # Level 1: 仅依赖 Level 0
        "accounts",                    # FK: users, llm_configurations
        "user_auth_sessions",          # FK: users
        "user_subscriptions",          # FK: users
        "user_exchange_config",        # FK: users
        "visual_strategies",           # FK: users
        "atas_strategies",             # FK: users

        # Level 2: 依赖 Level 1
        "ai_strategies",               # FK: prompt_templates, accounts
        "account_prompt_bindings",     # FK: accounts, prompt_templates
        "exchange_credentials",        # FK: accounts, users
        "full_auto_sessions",          # FK: accounts ×2
        "paper_balances",              # FK: accounts
        "paper_positions",             # FK: accounts
        "paper_orders",                # FK: accounts
        "paper_funding_ledger",        # FK: accounts
        "positions",                   # FK: accounts
        "orders",                      # FK: accounts
        "account_asset_snapshots",     # FK: accounts
        "account_strategy_configs",    # FK: accounts
        "hyperliquid_wallets",         # FK: accounts
        "hyperliquid_account_snapshots",# FK: accounts
        "hyperliquid_exchange_actions",# FK: accounts
        "risk_control_configs",        # FK: accounts
        "trade_memory_records",        # FK: accounts
        "trader_mental_states",        # FK: accounts
        "trader_personalities",        # FK: accounts
        "signal_trade_feedback",       # FK: accounts
        "strategy_memories",           # FK: ai_strategies
        "strategy_trades",             # FK: ai_strategies
        "prompt_training_records",     # FK: ai_strategies, prompt_templates
        "dingtalk_notifications",      # FK: dingtalk_bots, accounts
        "dingtalk_notification_stats", # FK: dingtalk_bots

        # Level 3: 依赖 Level 2
        "trades",                      # FK: orders, accounts
        "hyperliquid_positions",       # FK: accounts, orders
        "signal_performance_history",  # FK: signal_pools, ai_strategies
        "backtest_trades",             # FK: backtest_runs
        "auto_coin_selections",        # FK: full_auto_sessions
        "rebate_orders",               # FK: rebate_positions
    ]

    # 只迁移 PG 中也存在的表
    migratable = [t for t in sqlite_tables if t in pg_tables and not t.startswith("sqlite_")]

    # Build O(1) lookup: table_name → priority_index
    _PRIORITY_MAP = {t: i for i, t in enumerate(priority_order)}

    def sort_key(t):
        if t in _PRIORITY_MAP:
            return (0, _PRIORITY_MAP[t])
        return (1, t)

    ordered_tables = sorted(migratable, key=sort_key)

    logger.info(f"待迁移表: {len(ordered_tables)} 个")
    logger.info("=" * 60)
    logger.info("开始迁移")
    logger.info("=" * 60)

    for table_name in ordered_tables:
        logger.info(f"[{table_name}]")
        try:
            migrate_table(sqlite_eng, pg_eng, table_name, args.batch_size, args.dry_run, progress)
        except Exception as e:
            logger.error(f"  {table_name}: 迁移失败: {e}")

    if not args.dry_run:
        # 数据校验
        logger.info("=" * 60)
        logger.info("数据校验")
        logger.info("=" * 60)
        mismatches = []
        for table_name in ordered_tables:
            try:
                s_count = get_row_count(sqlite_eng, table_name)
                p_count = get_row_count(pg_eng, table_name)
                status = "OK" if s_count == p_count else f"MISMATCH({s_count} vs {p_count})"
                if s_count > 0 or p_count > 0:
                    logger.info(f"  {table_name}: SQLite={s_count}, PG={p_count} [{status}]")
                if s_count != p_count:
                    mismatches.append(table_name)
            except Exception as e:
                logger.warning(f"  {table_name}: 校验失败: {e}")

        if mismatches:
            logger.warning(f"行数不匹配的表: {len(mismatches)} 个")
            for m in mismatches:
                logger.warning(f"  - {m}")
        else:
            logger.info("所有表行数匹配!")

    logger.info("迁移完成")

    sqlite_eng.dispose()
    pg_eng.dispose()


if __name__ == "__main__":
    main()
