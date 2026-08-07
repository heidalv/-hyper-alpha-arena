"""Idempotent migration: create observation_pool_labels table (5.2 批量打标).

观察池批量打标（v6 10.2.3 本地 LLM）：auto_coin_selections 注入样本满足
min_samples=3 后由 batch_labeler 批量打标，结果落此表（selection_id 唯一，幂等）。
"""
from backend.database.connection import engine
from sqlalchemy import text, inspect


def upgrade():
    conn = engine.connect()
    try:
        inspector = inspect(engine)
        if "observation_pool_labels" not in inspector.get_table_names():
            conn.execute(text(
                """
                CREATE TABLE observation_pool_labels (
                    id SERIAL PRIMARY KEY,
                    selection_id INTEGER NOT NULL UNIQUE,
                    symbol VARCHAR(20) NOT NULL,
                    session_id VARCHAR(50),
                    llm_config_id INTEGER,
                    model VARCHAR(100),
                    regime_label VARCHAR(30),
                    sentiment_bias VARCHAR(20),
                    quality VARCHAR(20),
                    confidence FLOAT,
                    reasoning TEXT,
                    raw_json JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tenant_id INTEGER NOT NULL
                )
                """
            ))
            conn.execute(text(
                "CREATE INDEX ix_observation_pool_labels_selection_id "
                "ON observation_pool_labels (selection_id)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_observation_pool_labels_symbol "
                "ON observation_pool_labels (symbol)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_observation_pool_labels_tenant_id "
                "ON observation_pool_labels (tenant_id)"
            ))
            conn.commit()
            print("OK: Created observation_pool_labels table")
        else:
            print("SKIP: observation_pool_labels already exists")
    finally:
        conn.close()


if __name__ == "__main__":
    upgrade()
