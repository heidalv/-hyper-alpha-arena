# -*- coding: utf-8 -*-
"""asterdex 资金费是否固定 vs 其它所（近 7 天方差）。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from sqlalchemy import create_engine, text

e = create_engine(
    "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market",
    isolation_level="AUTOCOMMIT",
)
db = e.connect()
rows = db.execute(text(
    "SELECT symbol, exchange, MIN(funding_rate), MAX(funding_rate), "
    "COUNT(DISTINCT funding_rate), COUNT(*) "
    "FROM perp_funding "
    "WHERE symbol IN ('BTC','ETH','SOL') "
    "AND timestamp > (EXTRACT(EPOCH FROM NOW())*1000)::bigint - 7*86400000 "
    "GROUP BY symbol, exchange ORDER BY symbol, exchange"
)).fetchall()
for r in rows:
    print("  ", r)
print("DONE")
