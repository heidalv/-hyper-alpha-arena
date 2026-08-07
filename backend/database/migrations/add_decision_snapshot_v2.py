"""
DecisionSnapshot v2 columns — analytics DB (alpha_analytics.db).

Usage:
    python backend/database/migrations/add_decision_snapshot_v2.py
"""
import os
import sys

from sqlalchemy import inspect, text

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from backend.database.connection import analytics_engine  # noqa: E402


def column_exists(inspector, table: str, column: str) -> bool:
    try:
        return column in {col["name"] for col in inspector.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    inspector = inspect(analytics_engine)
    table = "decision_snapshots"
    columns = [
        ("proposal_id", "VARCHAR(64)"),
        ("trace_id", "VARCHAR(64)"),
        ("source_lane", "VARCHAR(32)"),
        ("proposal_json", "JSON"),
        ("evaluate_verdict_json", "JSON"),
        ("gate_blocks_json", "JSON"),
        ("orchestrator_json", "JSON"),
        ("executed", "BOOLEAN"),
        ("execution_channel", "VARCHAR(16)"),
        ("content_hash", "VARCHAR(64)"),
        ("prev_hash", "VARCHAR(64)"),
    ]
    with analytics_engine.connect() as conn:
        for col_name, col_type in columns:
            if not column_exists(inspector, table, col_name):
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                    print(f"Added {col_name} to {table}")
                except Exception as err:
                    print(f"Skip {col_name}: {err}")
            else:
                print(f"Exists {col_name}")
        conn.commit()
    print("DecisionSnapshot v2 migration done")


if __name__ == "__main__":
    upgrade()
