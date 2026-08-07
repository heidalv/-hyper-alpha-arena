#!/usr/bin/env python3
"""
Migration: Add V3 columns to arbitrage_positions

Adds fields needed by the unified arbitrage orchestrator:
- exchange_long/short for cross-exchange tracking
- entry_z_score, entry_spread_pct, entry_basis_pct for entry metrics
- liquidation prices and maintenance margin for risk management
- entry_edge for edge decay tracking
- mode for paper/live execution distinction
- size_usd and pnl for performance tracking
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine, text
from backend.database.connection import DATABASE_URL


COLUMNS = [
    ("funding_payments_count", "INTEGER DEFAULT 0"),
    ("exchange_long", "VARCHAR(32)"),
    ("exchange_short", "VARCHAR(32)"),
    ("entry_z_score", "DECIMAL(10, 4)"),
    ("entry_spread_pct", "DECIMAL(10, 8)"),
    ("entry_basis_pct", "DECIMAL(10, 8)"),
    ("liquidation_price_long", "DECIMAL(20, 8)"),
    ("liquidation_price_short", "DECIMAL(20, 8)"),
    ("maintenance_margin", "DECIMAL(20, 8)"),
    ("entry_edge", "DECIMAL(10, 8)"),
    ("mode", "VARCHAR(16) DEFAULT 'paper'"),
    ("size_usd", "DECIMAL(20, 8)"),
    ("pnl", "DECIMAL(20, 8)"),
]


def upgrade():
    """Add V3 columns to arbitrage_positions table"""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        for col_name, col_type in COLUMNS:
            # Check if column already exists
            result = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'arbitrage_positions' "
                f"AND column_name = '{col_name}'"
            ))
            if result.fetchone():
                print(f"Column {col_name} already exists, skipping")
                continue

            conn.execute(text(
                f"ALTER TABLE arbitrage_positions ADD COLUMN {col_name} {col_type}"
            ))
            print(f"Added column {col_name} ({col_type})")

        conn.commit()
        print("Migration completed: V3 columns added to arbitrage_positions")


def rollback():
    """Remove V3 columns from arbitrage_positions table"""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        for col_name, _ in reversed(COLUMNS):
            conn.execute(text(
                f"ALTER TABLE arbitrage_positions DROP COLUMN IF EXISTS {col_name}"
            ))
        conn.commit()
        print("Rollback completed: V3 columns removed from arbitrage_positions")


if __name__ == "__main__":
    upgrade()
