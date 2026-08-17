"""Migration: add runtime_state_json to system_coordinator_state (P1-5).

P1-5 �?unified_learning 的连�?streak / 交易计数器落库（原内存态，重启清零导致
连亏保护与提示词进化触发被重置）。幂等：列已存在则跳过�?"""

import logging

logger = logging.getLogger(__name__)


def upgrade():
    try:
        from backend.database.connection import engine
    except Exception:
        from backend.database.connection import engine
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "system_coordinator_state" not in tables:
        logger.info("system_coordinator_state table not found, skipping runtime_state_json")
        return
    columns = {col["name"] for col in inspector.get_columns("system_coordinator_state")}
    if "runtime_state_json" in columns:
        logger.info("runtime_state_json already exists on system_coordinator_state")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE system_coordinator_state ADD COLUMN runtime_state_json TEXT"))
        logger.info("Added runtime_state_json to system_coordinator_state")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()