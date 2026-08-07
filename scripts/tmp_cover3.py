# -*- coding: utf-8 -*-
import os
os.environ["DATA_CENTER_MODE"] = "standalone"
os.environ["MARKET_DATA_DC_ONLY"] = "true"
from sqlalchemy import text
from backend.database.connection import MarketSessionLocal
with MarketSessionLocal() as db:
    rows = db.execute(text("""
        SELECT exchange, period, COUNT(DISTINCT symbol) AS sym,
               ROUND((MAX("timestamp")-MIN("timestamp"))/86400.0) AS days
        FROM crypto_klines
        GROUP BY exchange, period ORDER BY exchange, period
    """)).fetchall()
print("各交易所各周期覆盖（币数/天数）:")
cur_ex = None
for r in rows:
    if r[0] != cur_ex:
        cur_ex = r[0]
        print(f"  [{cur_ex}]")
    print(f"    {r[1]:<5} sym={r[2]:>5} days={r[3]:>6}")
