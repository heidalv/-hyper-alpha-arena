# -*- coding: utf-8 -*-
"""查看 alpha_market 当前活动查询（判断 h422 跑到哪一步）。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT pid, state, LEFT(query, 110), (now() - query_start)
    FROM pg_stat_activity
    WHERE datname = 'alpha_market' AND pid <> pg_backend_pid() AND state IS NOT NULL
    ORDER BY query_start
""")
for pid, state, q, dur in cur.fetchall():
    print(f"[{pid}] {state} {dur} | {q}")
