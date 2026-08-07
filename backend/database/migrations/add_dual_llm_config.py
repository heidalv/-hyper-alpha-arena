"""Idempotent migration: add llm_config_id_deep column to accounts table.

Supports dual-model LLM configuration where each account can have
separate configs for quick tasks (llm_config_id) and deep reasoning (llm_config_id_deep).
"""
from backend.database.connection import engine
from sqlalchemy import text, inspect


def upgrade():
    conn = engine.connect()
    try:
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("accounts")]
        if "llm_config_id_deep" not in columns:
            conn.execute(text(
                "ALTER TABLE accounts ADD COLUMN llm_config_id_deep INTEGER REFERENCES llm_configurations(id)"
            ))
            # Also add to production DB if using different path
            try:
                conn.execute(text(
                    "ALTER TABLE accounts ADD COLUMN llm_config_name_deep VARCHAR(100)"
                ))
            except Exception:
                pass  # SQLite doesn't support ALTER ADD COLUMN constraints easily
            conn.commit()
            print("✓ Added llm_config_id_deep column to accounts")
        else:
            print("→ llm_config_id_deep already exists, skipping")
    finally:
        conn.close()
