"""
数据库拆分迁移脚本 (V4 §3.7)

将 alpha_arena.db 中的市场/分析表迁移到独立的数据库文件：
  alpha_market.db    — 市场数据 (高频写入)
  alpha_analytics.db — 分析审计 (异步写入)

用法:
  cd backend
  python -m database.migrate_split_db [--dry-run] [--skip-data]

--dry-run     只打印将执行的操作, 不实际迁移
--skip-data   只建表结构, 不迁移已有数据
"""

import argparse
import os
import sqlite3
import sys
import time
import shutil

# ── 项目路径 ──
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

CORE_DB = os.path.join(DATA_DIR, "alpha_arena.db")
MARKET_DB = os.path.join(DATA_DIR, "alpha_market.db")
ANALYTICS_DB = os.path.join(DATA_DIR, "alpha_analytics.db")

# ── 表分配 ──
MARKET_TABLES = [
    "crypto_prices",
    "crypto_klines",
    "crypto_price_ticks",
    "perp_funding",
    "price_samples",
    "market_trades_aggregated",
    "market_orderbook_snapshots",
    "symbol_aux_timeseries",
    "market_asset_metrics",
    "news_events",
    "whale_activities",
    "kline_collection_tasks",
]

ANALYTICS_TABLES = [
    "ai_decision_logs",
    "decision_retrospectives",
    "kline_ai_analysis_logs",
    "llm_usage_logs",
    "risk_control_events",
    "decision_snapshots",
    "factor_quality_reports",
    "generated_signal_history",
    "pattern_definitions",
    "market_analysis_snapshots",
    "strategy_analysis_logs",
    "strategy_optimization_logs",
]


def log(msg):
    print(f"[SPLIT-DB] {msg}")


def get_tables(conn):
    """获取数据库中所有用户表名。"""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def get_row_count(conn, table):
    """获取表行数。"""
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def get_schema(conn, table):
    """获取建表 SQL。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else None


def get_indexes(conn, table):
    """获取表的索引 SQL。"""
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def ensure_wal(conn, label):
    """确保 WAL 模式。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA synchronous=NORMAL")
    log(f"  {label}: WAL mode enabled")


def create_tables_in_target(src_conn, dst_conn, tables, label):
    """在目标数据库中创建表结构（不含数据）。"""
    created = 0
    existing = get_tables(dst_conn)
    for table in tables:
        if table in existing:
            log(f"  [{label}] {table}: already exists, skip DDL")
            continue
        schema = get_schema(src_conn, table)
        if not schema:
            log(f"  [{label}] {table}: not found in source, skip")
            continue
        dst_conn.execute(schema)
        # 创建索引
        for idx_sql in get_indexes(src_conn, table):
            try:
                dst_conn.execute(idx_sql)
            except Exception as e:
                log(f"  [{label}] index creation warning for {table}: {e}")
        created += 1
    log(f"  [{label}] Created {created} tables")
    return created


def migrate_data(src_conn, dst_conn, tables, label, batch_size=5000):
    """将源数据库表数据批量迁移到目标数据库。"""
    for table in tables:
        existing = get_tables(dst_conn)
        if table not in existing:
            log(f"  [{label}] {table}: table not in target, skip data")
            continue

        src_count = get_row_count(src_conn, table)
        dst_count = get_row_count(dst_conn, table)

        if dst_count >= src_count and src_count > 0:
            log(f"  [{label}] {table}: {dst_count} rows already >= source {src_count}, skip")
            continue

        if src_count == 0:
            log(f"  [{label}] {table}: empty in source, skip")
            continue

        # 获取列名
        cols = [desc[0] for desc in src_conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
        col_list = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)

        # 获取需要迁移的行数
        remaining = src_count - dst_count
        log(f"  [{label}] {table}: migrating {remaining} rows ({dst_count}/{src_count} already done)")

        # 使用 OFFSET 分批迁移
        offset = dst_count
        total_migrated = 0
        while True:
            rows = src_conn.execute(
                f'SELECT {col_list} FROM "{table}" ORDER BY rowid LIMIT {batch_size} OFFSET {offset}'
            ).fetchall()
            if not rows:
                break
            dst_conn.executemany(
                f'INSERT OR IGNORE INTO "{table}" ({col_list}) VALUES ({placeholders})', rows
            )
            total_migrated += len(rows)
            offset += len(rows)
            if len(rows) < batch_size:
                break

        dst_conn.commit()
        log(f"  [{label}] {table}: migrated {total_migrated} rows total")


def main():
    parser = argparse.ArgumentParser(description="Split alpha_arena.db into separate databases")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    parser.add_argument("--skip-data", action="store_true", help="Only create schemas, skip data migration")
    args = parser.parse_args()

    log(f"Project root: {PROJECT_ROOT}")
    log(f"Data dir:     {DATA_DIR}")

    if not os.path.exists(CORE_DB):
        log(f"ERROR: Core database not found: {CORE_DB}")
        sys.exit(1)

    core_size_mb = os.path.getsize(CORE_DB) / (1024 * 1024)
    log(f"Core DB: {CORE_DB} ({core_size_mb:.1f} MB)")

    # 打开源数据库
    src = sqlite3.connect(CORE_DB)
    src.row_factory = None
    ensure_wal(src, "Source")

    existing_tables = get_tables(src)
    log(f"Source tables: {len(existing_tables)} total")

    # ── 验证所有要迁移的表都存在 ──
    all_migrate = MARKET_TABLES + ANALYTICS_TABLES
    missing = [t for t in all_migrate if t not in existing_tables]
    if missing:
        log(f"WARNING: Tables not found in source (will skip): {missing}")

    # ── DRY RUN ──
    if args.dry_run:
        log("\n=== DRY RUN ===")
        for table in MARKET_TABLES:
            if table in existing_tables:
                count = get_row_count(src, table)
                log(f"  [Market] {table}: {count:,} rows -> {MARKET_DB}")
        for table in ANALYTICS_TABLES:
            if table in existing_tables:
                count = get_row_count(src, table)
                log(f"  [Analytics] {table}: {count:,} rows -> {ANALYTICS_DB}")
        src.close()
        log("Dry run complete. No changes made.")
        return

    # ── Backup ──
    backup_path = CORE_DB + f".pre-split.{int(time.time())}"
    log(f"Creating backup: {backup_path}")
    shutil.copy2(CORE_DB, backup_path)
    log(f"Backup size: {os.path.getsize(backup_path) / (1024*1024):.1f} MB")

    # ── Create target databases ──
    os.makedirs(DATA_DIR, exist_ok=True)

    market_conn = sqlite3.connect(MARKET_DB)
    analytics_conn = sqlite3.connect(ANALYTICS_DB)
    ensure_wal(market_conn, "Market")
    ensure_wal(analytics_conn, "Analytics")

    try:
        # ── Step 1: Create tables ──
        log("\n--- Step 1: Create table schemas ---")
        create_tables_in_target(src, market_conn, MARKET_TABLES, "Market")
        create_tables_in_target(src, analytics_conn, ANALYTICS_TABLES, "Analytics")

        if not args.skip_data:
            # ── Step 2: Migrate data ──
            log("\n--- Step 2: Migrate data ---")
            t0 = time.time()
            migrate_data(src, market_conn, MARKET_TABLES, "Market")
            migrate_data(src, analytics_conn, ANALYTICS_TABLES, "Analytics")
            elapsed = time.time() - t0
            log(f"Data migration completed in {elapsed:.1f}s")

        # ── Step 3: Report ──
        log("\n--- Results ---")
        for name, path, tables in [
            ("Market", MARKET_DB, MARKET_TABLES),
            ("Analytics", ANALYTICS_DB, ANALYTICS_TABLES),
        ]:
            if os.path.exists(path):
                size = os.path.getsize(path) / (1024 * 1024)
                log(f"  {name}: {path} ({size:.1f} MB)")
                conn = sqlite3.connect(path)
                for t in tables:
                    if t in get_tables(conn):
                        log(f"    {t}: {get_row_count(conn, t):,} rows")
                conn.close()

        log(f"\n  Core (remaining): {CORE_DB}")

    finally:
        market_conn.close()
        analytics_conn.close()
        src.close()

    log("\nMigration complete!")
    log(f"Backup at: {backup_path}")
    log("NOTE: Original tables remain in alpha_arena.db. Run with --drop-source to remove them after verification.")


if __name__ == "__main__":
    main()
