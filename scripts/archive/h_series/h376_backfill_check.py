# -*- coding: utf-8 -*-
"""h376 部署前检查：#2 新币的 mid_hist 回填数据可用性。"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

NEW = ["DOGE", "SUI", "NEAR", "ARB", "ADA", "XRP", "ENA"]
with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
    with c.cursor() as cur:
        for s in NEW:
            cur.execute("""
                SELECT count(*), max(timestamp),
                       max(timestamp) < (extract(epoch from now())*1000 - 3600*1000)::bigint
                FROM market_orderbook_snapshots
                WHERE exchange='asterdex' AND symbol=%s
                  AND timestamp > (extract(epoch from now())*1000 - 24*3600*1000)::bigint
            """, (s,))
            r = cur.fetchone()
            stale = "STALE(>1h)" if r[2] else "fresh"
            print(f"{s}: 24h rows={r[0]} last={r[1]} {stale}")
