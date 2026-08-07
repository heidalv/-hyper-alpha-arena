"""Add target_symbols, primary_symbol, timeframe to ai_strategies table.

Supports multi-symbol strategy trading. Idempotent - safe to run multiple times.
"""
import os
import sys

from sqlalchemy import inspect, text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

from backend.database.connection import engine  # noqa: E402


def column_exists(inspector, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = inspect(engine)
    table = "ai_strategies"

    if table not in inspector.get_table_names():
        print(f"ℹ️  Table {table} does not exist yet, skipping")
        return

    with engine.begin() as conn:
        columns_to_add = [
            ("target_symbols", "JSON DEFAULT NULL"),
            ("primary_symbol", "VARCHAR(20) DEFAULT 'BTC'"),
            ("timeframe", "VARCHAR(10) DEFAULT '15m'"),
        ]

        for col_name, col_def in columns_to_add:
            if not column_exists(inspector, table, col_name):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                print(f"✅ Added {col_name} to {table}")
            else:
                print(f"ℹ️  {col_name} already exists on {table}")

        # Set defaults for existing rows
        conn.execute(text(
            f"UPDATE {table} SET target_symbols = '[\"BTC\"]' WHERE target_symbols IS NULL"
        ))
        conn.execute(text(
            f"UPDATE {table} SET primary_symbol = 'BTC' WHERE primary_symbol IS NULL"
        ))
        conn.execute(text(
            f"UPDATE {table} SET timeframe = '15m' WHERE timeframe IS NULL"
        ))
        print("✅ Set default values for existing strategies")


if __name__ == "__main__":
    upgrade()
