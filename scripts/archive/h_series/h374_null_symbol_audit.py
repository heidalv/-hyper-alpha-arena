# -*- coding: utf-8 -*-
"""h374 审计：#2 判定分币种路径的 NULL symbol 崩溃风险检查。"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute("""
            SELECT count(*) FILTER (WHERE symbol IS NULL) AS null_sym,
                   count(*) AS total,
                   min(ts), max(ts)
            FROM lane_ledger WHERE lane_id='mm_asterdex'
        """)
        r = cur.fetchone()
        print(f"lane_ledger: null_symbol={r[0]} total={r[1]} range={r[2]}..{r[3]}")
        cur.execute("""
            SELECT count(*) FILTER (WHERE symbol IS NULL) AS null_sym, count(*) AS total
            FROM lane_ledger WHERE lane_id='mm_asterdex' AND ts > now() - interval '14 days'
        """)
        r = cur.fetchone()
        print(f"近 14 天: null_symbol={r[0]} total={r[1]}")
