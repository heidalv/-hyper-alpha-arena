# -*- coding: utf-8 -*-
"""Inspect alpha_analytics.market_analysis_snapshots: schema, row counts, timestamp epoch unit."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
import psycopg

ANALYTICS = "postgresql://laobao:alpha_pass@localhost:5432/alpha_analytics"
with psycopg.connect(ANALYTICS, autocommit=True) as c:
    cur = c.cursor()
    cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name='market_analysis_snapshots' ORDER BY ordinal_position")
    print("== schema ==")
    for r in cur.fetchall():
        print("  ", r[0], r[1])
    cur.execute("SELECT count(*) FROM market_analysis_snapshots")
    print("total rows:", cur.fetchone()[0])
    cur.execute("""SELECT symbol, count(*), min(created_at), max(created_at)
                   FROM market_analysis_snapshots GROUP BY symbol ORDER BY count(*) DESC LIMIT 20""")
    print("== per symbol ==")
    for r in cur.fetchall():
        print("  ", r)
    cur.execute("SELECT * FROM market_analysis_snapshots ORDER BY created_at DESC LIMIT 3")
    cols = [d[0] for d in cur.description]
    print("cols:", cols)
    for r in cur.fetchall():
        print("  ", dict(zip(cols, r)))
    cur.execute("SELECT * FROM market_analysis_snapshots ORDER BY created_at ASC LIMIT 3")
    for r in cur.fetchall():
        print("  old:", dict(zip(cols, r)))
