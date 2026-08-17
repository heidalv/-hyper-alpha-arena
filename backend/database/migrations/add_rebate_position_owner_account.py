"""
Migration: add owner_account_id to rebate_positions for unified account soft-link.

阶段 4.2 账户统一服务�? rebate_positions 表原本无 account FK（仅�?position_id 标识）�?本迁移新�?owner_account_id 列（nullable），建立�?arbitrage_paper_accounts.id 的软关联�?
- nullable: 老数据留空，不影响现有逻辑
- 新数据由 unified_account_service �?rebate_arb 引擎写入
- 可通过 DROP COLUMN 回滚（SQLite 3.35+ / Postgres�?
幂等: 检查列是否存在再添加�?"""
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


def upgrade():
    """Apply migration idempotently."""
    from backend.database.connection import engine
    from sqlalchemy import text

    with engine.begin() as conn:
        dialect = conn.dialect.name
        logger.info("Starting migration: add_rebate_position_owner_account (dialect=%s)", dialect)

        col = "owner_account_id"
        if not _column_exists(conn, "rebate_positions", col):
            col_type = "INTEGER" if dialect == "sqlite" else "INTEGER"
            conn.execute(text(
                f"ALTER TABLE rebate_positions ADD COLUMN {col} {col_type}"
            ))
            logger.info("Added rebate_positions.%s (nullable, 软关�?arbitrage_paper_accounts.id)", col)

            # 索引（便于按账户查询套利仓位�?            idx_name = "idx_rebate_positions_owner_account"
            try:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON rebate_positions({col})"
                ))
                logger.info("Created index %s", idx_name)
            except Exception as e:
                logger.warning("Index creation skipped: %s", e)
        else:
            logger.info("rebate_positions.%s already exists, skip", col)

    logger.info("Migration add_rebate_position_owner_account done")


if __name__ == "__main__":
    upgrade()
