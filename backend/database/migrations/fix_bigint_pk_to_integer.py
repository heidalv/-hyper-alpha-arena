#!/usr/bin/env python3
"""
Migration: Fix BIGINT primary keys → INTEGER for SQLite autoincrement

SQLite only auto-increments INTEGER PRIMARY KEY columns (rowid alias).
BIGINT PRIMARY KEY creates a separate column that does NOT auto-generate values,
causing "NOT NULL constraint failed" on INSERT.

Tables affected:
- coordinator_actions (id: BIGINT → INTEGER)
- auto_coin_selections (id: BIGINT → INTEGER)

Both tables are confirmed empty (0 rows), so safe to recreate.

IDEMPOTENT: Checks column type before applying. Safe to run multiple times.
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect
from connection import SessionLocal, engine
from backend.database.dialect import dialect

logger = logging.getLogger(__name__)

TABLES_TO_FIX = ["coordinator_actions", "auto_coin_selections"]


def upgrade():
    """Recreate tables with INTEGER PK instead of BIGINT (SQLite only)."""
    # PostgreSQL uses SERIAL/BIGSERIAL which handles auto-increment natively
    if not dialect.is_sqlite:
        logger.info("fix_bigint_pk_to_integer: 跳过（仅 SQLite 需要）")
        return

    logger.info("Starting migration: fix_bigint_pk_to_integer")

    db = SessionLocal()
    try:
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        for table_name in TABLES_TO_FIX:
            if table_name not in existing_tables:
                logger.info(f"Table '{table_name}' does not exist, skipping...")
                continue

            # Check column type via PRAGMA
            result = db.execute(text(f"PRAGMA table_info({table_name})"))
            columns = {row[1]: row[2] for row in result}  # name → type
            current_type = columns.get("id", "").upper()

            if current_type == "INTEGER":
                logger.info(f"Table '{table_name}'.id is already INTEGER, skipping...")
                continue

            logger.info(f"Fixing '{table_name}'.id from {current_type} to INTEGER...")

            # Get full column list (excluding id which we'll recreate)
            col_result = db.execute(text(f"PRAGMA table_info({table_name})"))
            col_defs = []
            col_names = []
            for row in col_result:
                cid, name, ctype, notnull, dflt, pk = row
                if name == "id":
                    # Replace BIGINT with INTEGER
                    col_defs.append(f'"id" INTEGER PRIMARY KEY AUTOINCREMENT')
                else:
                    col_def = f'"{name}" {ctype}'
                    if notnull:
                        col_def += " NOT NULL"
                    if dflt is not None:
                        col_def += f" DEFAULT {dflt}"
                    col_defs.append(col_def)
                col_names.append(name)

            columns_sql = ",\n    ".join(col_defs)

            # Check row count
            count_result = db.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            row_count = count_result.scalar()

            if row_count > 0:
                # Copy data: create temp table, copy, drop old, rename
                logger.info(f"  Table '{table_name}' has {row_count} rows, migrating with data preservation...")
                db.execute(text(f"ALTER TABLE {table_name} RENAME TO _{table_name}_old"))
                db.execute(text(f"CREATE TABLE {table_name} (\n    {columns_sql}\n)"))
                col_list = ", ".join(f'"{c}"' for c in col_names if c != "id")
                db.execute(text(
                    f"INSERT INTO {table_name} ({col_list}) "
                    f"SELECT {col_list} FROM _{table_name}_old"
                ))
                db.execute(text(f"DROP TABLE _{table_name}_old"))
            else:
                # No data: simple drop + recreate
                logger.info(f"  Table '{table_name}' is empty, recreating...")
                db.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
                db.execute(text(f"CREATE TABLE {table_name} (\n    {columns_sql}\n)"))

            # Verify
            verify = db.execute(text(f"PRAGMA table_info({table_name})"))
            new_type = None
            for row in verify:
                if row[1] == "id":
                    new_type = row[2]
            logger.info(f"  '{table_name}'.id type is now: {new_type}")

        db.commit()
        logger.info("Migration fix_bigint_pk_to_integer completed successfully!")

    except Exception as e:
        db.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        db.close()


def downgrade():
    """Not supported - INTEGER→BIGINT would break autoincrement"""
    logger.warning("Downgrade not supported for this migration")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
