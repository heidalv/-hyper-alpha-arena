# -*- coding: utf-8 -*-
"""找账户权益历史快照 + 当前未实现亏损，对齐用户看到的 -20u。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal
from sqlalchemy import create_engine, text

print("=== alpha_snapshots 里的表 ===")
e = create_engine(
    "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_snapshots",
    isolation_level="AUTOCOMMIT",
)
db = e.connect()
for r in db.execute(text(
    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
)):
    print("  ", r[0])

with system_identity(), SessionLocal() as db2:
    print("\n=== 账户 14 当前未平仓持仓的未实现盈亏 ===")
    for r in db2.execute(text(
        "SELECT COUNT(*), ROUND(SUM(COALESCE(unrealized_pnl,0))::numeric,2) "
        "FROM paper_positions WHERE account_id=14 AND status='open'"
    )):
        print("  开仓数/未实现合计:", r)

    print("\n=== 账户 14 当前开仓持仓明细（浮亏排序）===")
    for r in db2.execute(text(
        "SELECT symbol, side, size, ROUND(entry_price::numeric,4), "
        "ROUND(mark_price::numeric,4), ROUND(unrealized_pnl::numeric,2), "
        "ROUND((unrealized_pnl/NULLIF(margin,0))::numeric,3) AS roi_pct, "
        "timeframe_tier, trade_nature, to_char(opened_at,'MM-DD HH24:MI') "
        "FROM paper_positions WHERE account_id=14 AND status='open' "
        "ORDER BY unrealized_pnl LIMIT 12"
    )):
        print("  ", r)

    print("\n=== 账户 14 昨日平仓持仓的 close_price 是否全部有对应订单（filled_at 为 NULL 的平仓单）===")
    for r in db2.execute(text(
        "SELECT COUNT(*) FROM paper_orders "
        "WHERE close_reason IS NOT NULL AND pnl IS NOT NULL AND filled_at IS NULL"
    )):
        print("  全期 filled_at NULL 的平仓单:", r)
print("DONE")
