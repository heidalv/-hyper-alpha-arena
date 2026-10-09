# -*- coding: utf-8 -*-
"""诊断：列出 alpha DB 当前活动查询。"""
import sys
sys.path.insert(0, ".")
from scripts.h367_coin_signature import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("""
            SELECT pid, state, age(clock_timestamp(), query_start) AS age,
                   left(query, 140) AS q
            FROM pg_stat_activity
            WHERE datname LIKE 'alpha%' AND state <> 'idle'
            ORDER BY query_start
        """)
        for r in cur.fetchall():
            print(r)
