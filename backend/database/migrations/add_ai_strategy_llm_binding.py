"""Idempotent migration: add per-strategy LLM binding columns to ai_strategies.

Strategy-level LLM override (quick/deep). When empty, runtime falls back to
the owning account's binding, then to the global default configuration.
"""
from backend.database.connection import engine
from sqlalchemy import text, inspect


def upgrade():
    conn = engine.connect()
    try:
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("ai_strategies")]
        if "llm_config_id" not in columns:
            conn.execute(text(
                "ALTER TABLE ai_strategies ADD COLUMN llm_config_id INTEGER "
                "REFERENCES llm_configurations(id)"
            ))
            print("✓ Added llm_config_id to ai_strategies")
        else:
            print("→ llm_config_id already exists, skipping")
        if "llm_config_id_deep" not in columns:
            conn.execute(text(
                "ALTER TABLE ai_strategies ADD COLUMN llm_config_id_deep INTEGER "
                "REFERENCES llm_configurations(id)"
            ))
            print("✓ Added llm_config_id_deep to ai_strategies")
        else:
            print("→ llm_config_id_deep already exists, skipping")
        conn.commit()
    finally:
        conn.close()
