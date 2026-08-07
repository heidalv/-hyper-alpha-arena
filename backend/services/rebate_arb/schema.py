"""Schema helpers for Rebate arbitrage services.

FastAPI startup already calls ``Base.metadata.create_all``. These helpers keep
standalone scripts and service-level tests from failing on older SQLite files.
"""

from __future__ import annotations

import logging
import threading

from backend.database.connection import Base, engine
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

_schema_lock = threading.Lock()
_schema_ready = False


def ensure_rebate_schema() -> None:
    """Create missing Rebate/RuleSync tables in legacy local databases."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            # Import registers models on Base.metadata.
            import backend.database.models  # noqa: F401
            Base.metadata.create_all(bind=engine)
            _ensure_optional_columns()
            _schema_ready = True
        except Exception as exc:
            logger.warning("[RebateSchema] ensure schema failed: %s", exc)
            raise


def _ensure_optional_columns() -> None:
    required = [
        ("arbitrage_profiles", "paper_account_mode", "VARCHAR(32) DEFAULT 'legacy_ai_paper'"),
        ("arbitrage_profiles", "arbitrage_paper_account_id", "INTEGER"),
        ("arbitrage_profiles", "strategy_llm_config_id", "INTEGER"),
        ("arbitrage_profiles", "execution_llm_config_id", "INTEGER"),
        ("full_auto_sessions", "paper_account_mode", "VARCHAR(32) DEFAULT 'legacy_ai_paper'"),
        ("full_auto_sessions", "arbitrage_paper_account_id", "INTEGER"),
    ]
    is_sqlite = str(engine.url).startswith("sqlite")
    with engine.begin() as conn:
        for table, col_name, col_def in required:
            try:
                if is_sqlite:
                    result = conn.execute(sa_text(f"PRAGMA table_info({table})"))
                    existing = {row[1] for row in result}
                else:
                    result = conn.execute(sa_text(
                        "SELECT column_name FROM information_schema.columns "
                        f"WHERE table_name = '{table}'"
                    ))
                    existing = {row[0] for row in result}
                if col_name not in existing:
                    conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
            except Exception as exc:
                logger.debug("[RebateSchema] optional column check %s.%s failed: %s", table, col_name, exc)
