# -*- coding: utf-8 -*-
import os
os.environ["DATA_CENTER_MODE"] = "standalone"
os.environ["MARKET_DATA_DC_ONLY"] = "true"
from datetime import datetime, timezone

from sqlalchemy import text

from backend.database.connection import MarketSessionLocal

now = int(datetime.now(timezone.utc).timestamp())
print(f"current UTC: {datetime.now(timezone.utc).isoformat()}")

with MarketSessionLocal() as db:
    rows = db.execute(text("""
        SELECT exchange, period,
               COUNT(DISTINCT symbol) AS symbols,
               MIN("timestamp") AS min_ts,
               MAX("timestamp") AS max_ts,
               COUNT(*) AS bars
        FROM crypto_klines
        GROUP BY exchange, period
        ORDER BY exchange, period
    """)).fetchall()

period_sec = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000,
}
print(f"{'exch':<10}{'period':<6}{'sym':>6}{'bars':>10}{'days':>9}{'fresh_min':>10}")
for r in rows:
    ex, period = r[0], r[1]
    symbols, bars, min_ts, max_ts = r[2], r[5], r[3] or 0, r[4] or 0
    days = (max_ts - min_ts) / 86400.0
    fresh_min = (now - max_ts) / 60.0
    print(f"{ex:<10}{period:<6}{symbols:>6}{bars:>10}{days:>9.1f}{fresh_min:>10.1f}")
