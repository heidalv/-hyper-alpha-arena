"""Migration: add exchange to paper_orders.

Paper orders must remember the exchange selected when the order was created.
Otherwise a pending order could be matched using a different exchange after the
trader changes account settings.
"""

import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        if conn.dialect.name == "sqlite":
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(row[1] == column for row in rows)
        rows = conn.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name='{table}' AND column_name='{column}'"
        ).fetchall()
        return len(rows) > 0
    except Exception:
        return False


def _table_exists(conn, table: str) -> bool:
    try:
        if conn.dialect.name == "sqlite":
            rows = conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            ).fetchall()
            return len(rows) > 0
        row = conn.execute(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='{table}')"
        ).fetchone()
        return bool(row[0])
    except Exception:
        return False


def upgrade():
    try:
        from backend.database.connection import engine
    except Exception:
        from database.connection import engine
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "paper_orders" not in tables:
        logger.info("paper_orders table not found, skipping exchange column")
        return
    columns = {col["name"] for col in inspector.get_columns("paper_orders")}
    if "exchange" in columns:
        logger.info("exchange already exists on paper_orders")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE paper_orders ADD COLUMN exchange VARCHAR(32)"))
        try:
            conn.execute(text(
                "UPDATE paper_orders "
                "SET exchange = COALESCE((SELECT selected_exchange FROM accounts WHERE accounts.id = paper_orders.account_id), 'hyperliquid') "
                "WHERE exchange IS NULL"
            ))
        except Exception as exc:
            logger.warning("paper_orders.exchange backfill warning: %s", exc)
        logger.info("Added exchange to paper_orders")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
