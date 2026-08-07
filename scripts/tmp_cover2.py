# -*- coding: utf-8 -*-
import os
os.environ["DATA_CENTER_MODE"] = "standalone"
os.environ["MARKET_DATA_DC_ONLY"] = "true"
from sqlalchemy import text
from backend.database.connection import MarketSessionLocal

with MarketSessionLocal() as db:
    rows = db.execute(text("""
        SELECT exchange, period, COUNT(*) AS bars, COUNT(DISTINCT symbol) AS sym,
               MAX("timestamp") AS max_ts
        FROM crypto_klines
        WHERE exchange IN ('bybit','okx')
        GROUP BY exchange, period ORDER BY exchange, period
    """)).fetchall()
print("bybit/okx 当前覆盖:")
for r in rows:
    print(f"  {r[0]:<8} {r[1]:<5} bars={r[2]:>8} sym={r[3]:>5}")
