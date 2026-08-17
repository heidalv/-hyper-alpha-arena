"""Migration: add tier to paper_funding_ledger (P0-8 资金费结算口径审�?.

P0-8 使资金费结算不再绑定 research 档：demo 档也�?ledger。tier 列记录持仓周期档位，
供后�?dry-run 对拍（纸�?vs 实盘资金费）分档统计。幂等：列已存在则跳过�?"""

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
    if "paper_funding_ledger" not in tables:
        logger.info("paper_funding_ledger table not found, skipping tier column")
        return
    columns = {col["name"] for col in inspector.get_columns("paper_funding_ledger")}
    if "tier" in columns:
        logger.info("tier already exists on paper_funding_ledger")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE paper_funding_ledger ADD COLUMN tier VARCHAR(16)"))
        logger.info("Added tier to paper_funding_ledger")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
