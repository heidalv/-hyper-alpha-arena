#!/usr/bin/env python3
"""
修复 PostgreSQL 数据迁移缺口

背景：初次迁移时 priority_order 不完整，导致部分表的行因 FK 违反
（父表尚未迁移）而静默失败。本脚本按正确的 FK 拓扑顺序重新迁移这些表。

用法:
    python fix_migration_gaps.py [--sqlite-url SQLITE_URL] [--pg-url PG_URL] [--dry-run]

工作方式:
    1. 按 FK 拓扑顺序列出所有有依赖关系的表
    2. 对每个表，只迁移 PG 中尚不存在的行（断点续传）
    3. 迁移后进行行数校验
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import logging
from migrate_to_postgresql import (
    get_sqlite_tables, get_pg_tables, get_table_columns,
    get_boolean_columns, get_row_count, load_progress, save_progress
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gap_fix")


# FK 拓扑排序：Level 0 → Level 1 → Level 2 → Level 3
# Level 0: 无 FK 依赖（如果第一次迁移失败，可能因为 PG schema 未创建）
GAP_PRIORITY = [
    # Level 0: 独立表（父表）
    "users",
    "llm_configurations",
    "prompt_templates",
    "signal_pools",
    "signal_definitions",
    "dingtalk_bots",
    "backtest_runs",
    "rebate_positions",
    "system_configs",

    # Level 1: 仅依赖 Level 0
    "accounts",
    "user_auth_sessions",
    "user_subscriptions",
    "user_exchange_config",
    "ai_prompt_conversations",
    "ai_signal_conversations",
    "ai_attribution_conversations",
    "visual_strategies",
    "atas_strategies",

    # Level 2: 依赖 Level 1
    "account_prompt_bindings",
    "ai_strategies",
    "dingtalk_notifications",
    "dingtalk_notification_stats",
    "exchange_credentials",
    "full_auto_sessions",
    "paper_balances",
    "paper_positions",
    "paper_orders",
    "paper_funding_ledger",
    "positions",
    "orders",
    "account_asset_snapshots",
    "account_strategy_configs",
    "hyperliquid_wallets",
    "hyperliquid_account_snapshots",
    "hyperliquid_exchange_actions",
    "risk_control_configs",
    "trade_memory_records",
    "trader_mental_states",
    "trader_personalities",
    "signal_trade_feedback",
    "ai_prompt_messages",
    "ai_signal_messages",
    "ai_attribution_messages",
    "strategy_memories",
    "strategy_trades",
    "prompt_training_records",

    # Level 3: 依赖 Level 2
    "signal_performance_history",
    "trades",
    "hyperliquid_positions",
    "strategy_executions",
    "backtest_trades",
    "auto_coin_selections",
    "rebate_orders",
]


def migrate_gap_table(sqlite_eng, pg_eng, table_name, batch_size, dry_run, progress):
    """迁移单个表 — 只迁移 PG 中不存在的行（通过主键去重）。"""
    from sqlalchemy import text, inspect

    # 获取列名交集
    sqlite_cols = get_table_columns(sqlite_eng, table_name)
    pg_cols = get_table_columns(pg_eng, table_name)
    common_cols = [c for c in sqlite_cols if c in pg_cols]

    if not common_cols:
        logger.warning(f"  {table_name}: 无公共列，跳过")
        return

    # 尝试获取主键列（用于去重）
    inspector = inspect(pg_eng)
    pk_cols = []
    try:
        pk_info = inspector.get_pk_constraint(table_name)
        pk_cols = pk_info.get("constrained_columns", []) if pk_info else []
    except Exception:
        pass

    sqlite_count = get_row_count(sqlite_eng, table_name)
    pg_count = get_row_count(pg_eng, table_name)

    if sqlite_count == 0:
        logger.info(f"  {table_name}: SQLite 无数据，跳过")
        return

    gap = sqlite_count - pg_count
    if gap <= 0:
        logger.info(f"  {table_name}: 无缺口 (SQLite={sqlite_count}, PG={pg_count})")
        return

    logger.info(f"  {table_name}: 缺口 {gap} 行 (SQLite={sqlite_count}, PG={pg_count})")

    if dry_run:
        return

    cols_str = ", ".join(f'"{c}"' for c in common_cols)
    params_str = ", ".join(f":{c}" for c in common_cols)

    # 如果有主键，使用 ON CONFLICT 去重
    if pk_cols and all(pk in common_cols for pk in pk_cols):
        pk_str = ", ".join(f'"{pk}"' for pk in pk_cols)
        insert_sql = text(
            f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({params_str}) '
            f'ON CONFLICT ({pk_str}) DO NOTHING'
        )
        use_conflict = True
    else:
        insert_sql = text(
            f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({params_str})'
        )
        use_conflict = False

    pg_bool_cols = get_boolean_columns(pg_eng, table_name)

    # 断点续传
    offset = progress.get(table_name, {}).get("offset", 0)
    migrated = offset
    failed = 0
    inserted = 0

    while migrated < sqlite_count:
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

        for row in rows:
            params = {}
            for i, col in enumerate(common_cols):
                val = row[i]
                if col in pg_bool_cols and val is not None:
                    params[col] = bool(val)
                else:
                    params[col] = val
            try:
                with pg_eng.begin() as pg_conn:
                    result = pg_conn.execute(insert_sql, params)
                    inserted += 1
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
        logger.info(f"  {table_name}: {migrated}/{sqlite_count} ({pct}%) "
                     f"插入={inserted}, 失败={failed}")

    # 最终校验
    final_pg_count = get_row_count(pg_eng, table_name)
    final_gap = sqlite_count - final_pg_count
    status = "OK" if final_gap == 0 else f"仍缺 {final_gap} 行"
    logger.info(f"  {table_name}: 完成, PG={final_pg_count}, {status}")


def main():
    parser = argparse.ArgumentParser(description="修复 PG 迁移数据缺口")
    parser.add_argument("--sqlite-url", default=None,
                        help="SQLite 连接串")
    parser.add_argument("--pg-url", default=None,
                        help="PostgreSQL 连接串")
    parser.add_argument("--batch-size", type=int, default=5000,
                        help="每批迁移行数")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅检查缺口不迁移")
    parser.add_argument("--reset", action="store_true",
                        help="清除断点进度")
    args = parser.parse_args()

    from sqlalchemy import create_engine

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

    sqlite_tables = get_sqlite_tables(sqlite_eng)
    pg_tables = get_pg_tables(pg_eng)
    logger.info(f"SQLite 表: {len(sqlite_tables)} 个, PG 表: {len(pg_tables)} 个")

    PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "..", ".gap_fix_progress.json")
    global save_progress

    def save_progress_local(progress):
        import json
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        logger.info("断点进度已清除")

    progress = {}
    if os.path.exists(PROGRESS_FILE):
        import json
        with open(PROGRESS_FILE) as f:
            progress = json.load(f)

    # 筛选需要修复的表：在 GAP_PRIORITY 中且两库都存在
    target_tables = [t for t in GAP_PRIORITY if t in sqlite_tables and t in pg_tables]

    # 检查缺口
    total_gap = 0
    logger.info("=" * 60)
    logger.info("数据缺口分析")
    logger.info("=" * 60)
    for table_name in target_tables:
        try:
            s_count = get_row_count(sqlite_eng, table_name)
            p_count = get_row_count(pg_eng, table_name)
            gap = s_count - p_count
            if gap > 0:
                logger.info(f"  {table_name}: SQLite={s_count}, PG={p_count}, 缺口={gap}")
                total_gap += gap
        except Exception as e:
            logger.warning(f"  {table_name}: 检查失败: {e}")

    logger.info(f"总缺口: {total_gap} 行")

    if total_gap == 0:
        logger.info("无数据缺口，无需修复！")
        sqlite_eng.dispose()
        pg_eng.dispose()
        return

    if args.dry_run:
        logger.info("--dry-run 模式，不执行实际迁移")
        sqlite_eng.dispose()
        pg_eng.dispose()
        return

    logger.info("=" * 60)
    logger.info("开始修复缺口")
    logger.info("=" * 60)

    for table_name in target_tables:
        logger.info(f"[{table_name}]")
        try:
            migrate_gap_table(sqlite_eng, pg_eng, table_name, args.batch_size, args.dry_run, progress)
        except Exception as e:
            logger.error(f"  {table_name}: 修复失败: {e}")

    # 最终校验
    logger.info("=" * 60)
    logger.info("修复后校验")
    logger.info("=" * 60)
    remaining_gap = 0
    for table_name in target_tables:
        try:
            s_count = get_row_count(sqlite_eng, table_name)
            p_count = get_row_count(pg_eng, table_name)
            gap = s_count - p_count
            if gap > 0:
                logger.warning(f"  {table_name}: 仍缺 {gap} 行")
                remaining_gap += gap
            elif gap == 0 and s_count > 0:
                logger.info(f"  {table_name}: 匹配 (={s_count})")
        except Exception as e:
            logger.warning(f"  {table_name}: 校验失败: {e}")

    if remaining_gap == 0:
        logger.info("所有缺口已修复！")
    else:
        logger.warning(f"仍有 {remaining_gap} 行缺口未修复")

    sqlite_eng.dispose()
    pg_eng.dispose()


if __name__ == "__main__":
    main()
