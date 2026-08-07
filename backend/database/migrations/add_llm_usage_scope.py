"""Idempotent migration: add usage_scope column to llm_configurations table.

usage_scope stores comma-separated usage keys so the system can route
non-trading LLM tasks (AI coin selection, factor mining, journal, assistant,
K-line analysis, evolution, news intel) to a specific configuration in the
backend instead of always using the global default.
"""
from backend.database.connection import engine
from sqlalchemy import text, inspect


def upgrade():
    conn = engine.connect()
    try:
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("llm_configurations")]
        if "usage_scope" not in columns:
            conn.execute(text(
                "ALTER TABLE llm_configurations ADD COLUMN usage_scope VARCHAR(500)"
            ))
            conn.commit()
            print("✓ Added usage_scope column to llm_configurations")
        else:
            print("→ usage_scope already exists, skipping")
    finally:
        conn.close()
