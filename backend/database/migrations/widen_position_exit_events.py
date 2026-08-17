"""
Migration: widen position_exit_events.exit_channel varchar(40) -> varchar(100).

�?reason（如 mlto_invalidation 长文）曾超过 varchar(40) 触发
StringDataRightTruncation，导致整笔平仓事务回滚。代码侧已有截断兜底�?此处把列加宽作双保险。幂等：仅当当前长度 < 100 时执行；SQLite 无长度约束，跳过�?"""
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


def upgrade():
    from sqlalchemy import text
    from backend.database.connection import engine

    with engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            logger.info("widen_position_exit_events: skip (dialect=%s)", conn.dialect.name)
            return
        rows = conn.execute(
            text(
                "SELECT character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name='position_exit_events' AND column_name='exit_channel'"
            )
        ).fetchall()
        cur = int(rows[0][0]) if rows and rows[0][0] is not None else 0
        if cur >= 100:
            logger.info("widen_position_exit_events: already %s, skip", cur)
            return
        conn.execute(
            text(
                "ALTER TABLE position_exit_events "
                "ALTER COLUMN exit_channel TYPE VARCHAR(100)"
            )
        )
        logger.info("widen_position_exit_events: exit_channel %s -> 100", cur)
