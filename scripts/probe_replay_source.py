# -*- coding: utf-8 -*-
"""影子回放零成交排查：asterdex 在 market_orderbook_snapshots 有没有数据。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                    autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT exchange, COUNT(*), MAX(timestamp), MIN(timestamp)
    FROM market_orderbook_snapshots
    WHERE timestamp > (EXTRACT(EPOCH FROM now())*1000 - 12*3600*1000)::bigint
    GROUP BY 1 ORDER BY 2 DESC
""")
print("近 12h market_orderbook_snapshots by exchange:")
for r in cur.fetchall():
    print("  ", r)

cur.execute("""
    SELECT COUNT(*) FROM market_orderbook_snapshots
    WHERE exchange='asterdex' AND symbol='BTCUSDT'
""")
print("\nasterdex BTCUSDT 全表行数:", cur.fetchone()[0])
