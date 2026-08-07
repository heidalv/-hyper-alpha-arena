#!/usr/bin/env python3
"""
Migration: Create rebate arbitrage tables

Creates 4 new tables for the rebate arbitrage system:
- rebate_positions: Position lifecycle tracking
- rebate_orders: Individual order records with exchange IDs
- rebate_incentive_snapshots: Time-series incentive data
- rebate_performance_logs: Historical performance records
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine, text
from backend.database.connection import DATABASE_URL
from backend.database.dialect import dialect


_SQLITE_AUTO_PK = "INTEGER PRIMARY KEY AUTOINCREMENT"
_DIALECT_PK = dialect.auto_pk()


TABLES = {
    "rebate_positions": """
        CREATE TABLE IF NOT EXISTS rebate_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id VARCHAR(64) UNIQUE NOT NULL,
            strategy_type VARCHAR(8) NOT NULL,
            source_exchange VARCHAR(32) NOT NULL,
            target_exchange VARCHAR(32),
            symbol VARCHAR(32) NOT NULL,
            side_a_size REAL DEFAULT 0.0,
            side_b_size REAL DEFAULT 0.0,
            entry_price_a REAL DEFAULT 0.0,
            entry_price_b REAL DEFAULT 0.0,
            current_pnl REAL DEFAULT 0.0,
            accumulated_rebate REAL DEFAULT 0.0,
            accumulated_points REAL DEFAULT 0.0,
            entry_time REAL NOT NULL,
            close_time REAL,
            max_hold_seconds REAL DEFAULT 2592000.0,
            status VARCHAR(16) DEFAULT 'active',
            paper_mode BOOLEAN DEFAULT 1,
            metadata_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "rebate_orders": """
        CREATE TABLE IF NOT EXISTS rebate_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id VARCHAR(64) NOT NULL REFERENCES rebate_positions(position_id),
            exchange VARCHAR(32) NOT NULL,
            leg VARCHAR(2) NOT NULL,
            exchange_order_id VARCHAR(128),
            symbol VARCHAR(32) NOT NULL,
            side VARCHAR(8) NOT NULL,
            order_type VARCHAR(16) NOT NULL,
            size REAL NOT NULL,
            price REAL,
            filled_size REAL DEFAULT 0.0,
            filled_price REAL DEFAULT 0.0,
            status VARCHAR(16) DEFAULT 'pending',
            fee_paid REAL DEFAULT 0.0,
            rebate_received REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "rebate_incentive_snapshots": """
        CREATE TABLE IF NOT EXISTS rebate_incentive_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange VARCHAR(32) NOT NULL,
            snapshot_time TIMESTAMP NOT NULL,
            fee_tier_name VARCHAR(32),
            maker_rate REAL DEFAULT 0.0,
            taker_rate REAL DEFAULT 0.0,
            rebate_rate REAL DEFAULT 0.0,
            points_balance REAL DEFAULT 0.0,
            points_multiplier REAL DEFAULT 1.0,
            volume_30d REAL DEFAULT 0.0,
            data_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "rebate_performance_logs": """
        CREATE TABLE IF NOT EXISTS rebate_performance_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id VARCHAR(64) NOT NULL,
            strategy_type VARCHAR(8) NOT NULL,
            total_pnl REAL DEFAULT 0.0,
            total_rebate REAL DEFAULT 0.0,
            total_points REAL DEFAULT 0.0,
            hold_hours REAL DEFAULT 0.0,
            close_reason VARCHAR(64),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_rebate_positions_strategy ON rebate_positions(strategy_type)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_positions_status ON rebate_positions(status)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_positions_exchange ON rebate_positions(source_exchange)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_orders_position ON rebate_orders(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_orders_exchange ON rebate_orders(exchange)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_orders_status ON rebate_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_snapshots_exchange ON rebate_incentive_snapshots(exchange)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_snapshots_time ON rebate_incentive_snapshots(snapshot_time)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_perf_position ON rebate_performance_logs(position_id)",
    "CREATE INDEX IF NOT EXISTS idx_rebate_perf_strategy ON rebate_performance_logs(strategy_type)",
]


def upgrade():
    """Create rebate arbitrage tables and indexes."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        for table_name, ddl in TABLES.items():
            # Replace SQLite-specific AUTOINCREMENT with dialect-appropriate PK
            adapted_ddl = ddl.replace(_SQLITE_AUTO_PK, _DIALECT_PK)
            conn.execute(text(adapted_ddl))
            print(f"Created table: {table_name}")

        for idx_sql in INDEXES:
            conn.execute(text(idx_sql))

        conn.commit()
        print(f"Migration completed: {len(TABLES)} tables, {len(INDEXES)} indexes created")


def rollback():
    """Drop rebate arbitrage tables."""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        for table_name in reversed(list(TABLES.keys())):
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            print(f"Dropped table: {table_name}")
        conn.commit()
        print("Rollback completed: rebate tables dropped")


if __name__ == "__main__":
    upgrade()
