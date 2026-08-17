"""
Migration: position exit state for dual-agent reversal protection.

Adds durable peak-profit / health / exit-state fields to paper_positions and
creates position_exit_events for partial/final exit quality tracking.
"""
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


def _column_exists(conn, table: str, column: str) -> bool:
    from sqlalchemy import text
    try:
        if conn.dialect.name == "sqlite":
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return any(row[1] == column for row in rows)
        rows = conn.execute(
            text(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name='{table}' AND column_name='{column}'"
            )
        ).fetchall()
        return bool(rows)
    except Exception:
        return False


def _index_exists(conn, index_name: str) -> bool:
    from sqlalchemy import text
    try:
        if conn.dialect.name == "sqlite":
            rows = conn.execute(
                text(f"SELECT name FROM sqlite_master WHERE type='index' AND name='{index_name}'")
            ).fetchall()
            return bool(rows)
        rows = conn.execute(
            text(
            "SELECT indexname FROM pg_indexes "
            f"WHERE indexname='{index_name}'"
            )
        ).fetchall()
        return bool(rows)
    except Exception:
        return False


def upgrade():
    """Apply migration idempotently."""
    from backend.database.connection import engine
    from sqlalchemy import text

    with engine.begin() as conn:
        dialect = conn.dialect.name
        logger.info("Starting migration: add_position_exit_state (dialect=%s)", dialect)

        fields = [
            ("peak_unrealized_pnl", "DOUBLE PRECISION NOT NULL DEFAULT 0.0"),
            ("peak_pnl_pct", "DOUBLE PRECISION NOT NULL DEFAULT 0.0"),
            ("health_score", "DOUBLE PRECISION"),
            ("health_regime", "VARCHAR(30)"),
            ("exit_state_json", "TEXT"),
        ]
        for col, col_type in fields:
            if not _column_exists(conn, "paper_positions", col):
                conn.execute(text(f"ALTER TABLE paper_positions ADD COLUMN {col} {col_type}"))
                logger.info("Added paper_positions.%s", col)

        id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if dialect == "sqlite" else "SERIAL PRIMARY KEY"
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS position_exit_events (
                id {id_type},
                position_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                strategy_id VARCHAR(50),
                symbol VARCHAR(20) NOT NULL,
                side VARCHAR(10) NOT NULL,
                trade_nature VARCHAR(20),
                event_type VARCHAR(40) NOT NULL,
                quantity DOUBLE PRECISION,
                price DOUBLE PRECISION,
                pnl DOUBLE PRECISION,
                fee DOUBLE PRECISION,
                close_ratio DOUBLE PRECISION,
                peak_pnl_at_event DOUBLE PRECISION,
                peak_pnl_pct_at_event DOUBLE PRECISION,
                pnl_at_event DOUBLE PRECISION,
                pnl_pct_at_event DOUBLE PRECISION,
                retention_ratio DOUBLE PRECISION,
                health_score DOUBLE PRECISION,
                health_regime VARCHAR(30),
                reversal_level VARCHAR(40),
                exit_channel VARCHAR(40),
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))

        indexes = [
            ("idx_position_exit_events_position", "position_exit_events(position_id)"),
            ("idx_position_exit_events_account", "position_exit_events(account_id)"),
            ("idx_position_exit_events_symbol", "position_exit_events(symbol)"),
            ("idx_position_exit_events_type", "position_exit_events(event_type)"),
            ("idx_position_exit_events_created", "position_exit_events(created_at)"),
        ]
        for idx_name, idx_def in indexes:
            if not _index_exists(conn, idx_name):
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}"))

    logger.info("Migration add_position_exit_state completed successfully")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
