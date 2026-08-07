"""
Migration: Add decision_source column to ai_decision_logs (D1)
Idempotent — checks column existence before ALTER

Usage:
    python database/migrations/add_decision_source_tracking.py
"""
import sys
import os

from sqlalchemy import inspect, text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

from backend.database.connection import engine  # noqa: E402


def column_exists(inspector, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = inspect(engine)
    table = "ai_decision_logs"

    with engine.connect() as conn:
        if not column_exists(inspector, table, "decision_source"):
            conn.execute(text(
                "ALTER TABLE ai_decision_logs "
                "ADD COLUMN decision_source VARCHAR(20) NOT NULL DEFAULT 'llm'"
            ))
            conn.commit()
            print("Added decision_source column to ai_decision_logs")
        else:
            print("decision_source column already exists")


# Alias for direct execution
main = upgrade

if __name__ == "__main__":
    upgrade()
