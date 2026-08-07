"""
Migration: AI Trader Exchange Configuration
- Add selected_exchange to accounts table
- Add user_id to exchange_credentials for global credential management
- Add enhanced risk control fields to risk_control_configs
"""
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    try:
        if conn.dialect.name == "sqlite":
            result = conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            return any(row[1] == column for row in result)
        else:
            result = conn.execute(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_name='{table}' AND column_name='{column}'"
            ).fetchall()
            return len(result) > 0
    except Exception:
        return False


def _table_exists(conn, table: str) -> bool:
    """Check if a table exists."""
    try:
        if conn.dialect.name == "sqlite":
            result = conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            ).fetchall()
            return len(result) > 0
        else:
            result = conn.execute(
                f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='{table}')"
            ).fetchone()
            return result[0]
    except Exception:
        return False


def upgrade():
    """Apply the migration — idempotent."""
    from database.connection import engine
    from sqlalchemy import text, inspect

    inspector = inspect(engine)

    with engine.begin() as conn:
        dialect = conn.dialect.name
        logger.info("Starting migration: add_trader_exchange_config (dialect=%s)", dialect)

        # ── 1. accounts: add selected_exchange ──
        if not _column_exists(conn, "accounts", "selected_exchange"):
            conn.execute(text(
                "ALTER TABLE accounts ADD COLUMN selected_exchange VARCHAR(32) DEFAULT 'hyperliquid'"
            ))
            logger.info("Added selected_exchange to accounts")

            # Backfill based on existing exchange flags
            try:
                conn.execute(text(
                    "UPDATE accounts SET selected_exchange = 'hyperliquid' "
                    "WHERE hyperliquid_enabled = 'true'"
                ))
                conn.execute(text(
                    "UPDATE accounts SET selected_exchange = 'binance' "
                    "WHERE (hyperliquid_enabled IS NULL OR hyperliquid_enabled != 'true') "
                    "AND binance_enabled = 'true'"
                ))
                logger.info("Backfilled selected_exchange from existing exchange flags")
            except Exception as e:
                logger.warning("Backfill warning (non-critical): %s", e)
        else:
            logger.info("selected_exchange already exists on accounts")

        # ── 2. exchange_credentials: add user_id ──
        if not _column_exists(conn, "exchange_credentials", "user_id"):
            conn.execute(text(
                "ALTER TABLE exchange_credentials ADD COLUMN user_id INTEGER"
            ))
            logger.info("Added user_id to exchange_credentials")

            # Backfill user_id from accounts
            try:
                conn.execute(text(
                    "UPDATE exchange_credentials "
                    "SET user_id = (SELECT user_id FROM accounts WHERE accounts.id = exchange_credentials.account_id) "
                    "WHERE user_id IS NULL AND account_id IS NOT NULL"
                ))
                logger.info("Backfilled user_id from account relationships")
            except Exception as e:
                logger.warning("user_id backfill warning (non-critical): %s", e)
        else:
            logger.info("user_id already exists on exchange_credentials")

        # ── 3. risk_control_configs: add enhanced risk fields ──
        risk_fields = [
            ("max_trade_amount", "FLOAT DEFAULT 1000.0"),
            ("daily_trade_count_limit", "INTEGER DEFAULT 50"),
            ("max_concurrent_positions", "INTEGER DEFAULT 10"),
            ("per_symbol_max_position", "INTEGER DEFAULT 3"),
            ("global_stop_loss_pct", "FLOAT DEFAULT 0.10"),
            ("enable_trade_amount_limit", "VARCHAR(10) DEFAULT 'true'"),
            ("enable_trade_count_limit", "VARCHAR(10) DEFAULT 'true'"),
            ("enable_concurrent_position_limit", "VARCHAR(10) DEFAULT 'true'"),
        ]

        if _table_exists(conn, "risk_control_configs"):
            for col_name, col_type in risk_fields:
                if not _column_exists(conn, "risk_control_configs", col_name):
                    conn.execute(text(
                        f"ALTER TABLE risk_control_configs ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info("Added %s to risk_control_configs", col_name)
                else:
                    logger.info("%s already exists on risk_control_configs", col_name)
        else:
            logger.info("risk_control_configs table not found, skipping risk fields")

    logger.info("Migration add_trader_exchange_config completed successfully")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
