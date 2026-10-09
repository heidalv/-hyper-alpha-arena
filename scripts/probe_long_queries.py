# -*- coding: utf-8 -*-
"""只读：列出 alpha_market 中超过 60s 的查询与当前 book/trades/depth 查询。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT pid, state, LEFT(query, 90), EXTRACT(EPOCH FROM (now()-query_start))::int,
           wait_event_type, wait_event
    FROM pg_stat_activity
    WHERE datname='alpha_market' AND pid <> pg_backend_pid()
      AND query NOT ILIKE '%COMMIT%' AND state IS NOT NULL
      AND (query ILIKE '%book_ticker%' OR query ILIKE '%depth%' OR query ILIKE '%trades%'
           OR EXTRACT(EPOCH FROM (now()-query_start)) > 120)
    ORDER BY query_start
""")
for pid, state, q, secs, wet, we in cur.fetchall():
    print(f"[{pid}] {state} {secs}s wait={wet}/{we} | {q}")
print("---done---")
