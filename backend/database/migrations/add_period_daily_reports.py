"""Migration: create period_daily_reports（三周期统一日报，2026-08-19）。

幂等：表已存在则跳过。跨库安全（SQLAlchemy create，不写原生 DDL）。"""

import logging

logger = logging.getLogger(__name__)


def upgrade():
    from backend.database.connection import engine
    from backend.database.models import PeriodDailyReport
    from sqlalchemy import inspect

    inspector = inspect(engine)
    if "period_daily_reports" in set(inspector.get_table_names()):
        logger.info("period_daily_reports already exists, skipping")
        return
    try:
        PeriodDailyReport.__table__.create(bind=engine, checkfirst=True)
        logger.info("Created period_daily_reports")
    except Exception as e:
        logger.warning("Create period_daily_reports failed (will retry next startup): %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
