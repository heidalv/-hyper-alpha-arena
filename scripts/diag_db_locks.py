# -*- coding: utf-8 -*-
"""诊断：paper dashboard 慢/超时的 DB 侧原因（阻塞链 + idle-in-transaction）。"""
import sys
sys.path.insert(0, ".")
from scripts.h356_universe_trial import read_env_dsn  # noqa: E402
import psycopg  # noqa: E402

Q1 = """
SELECT pid, state, application_name,
       age(clock_timestamp(), state_change) AS state_age,
       age(clock_timestamp(), query_start) AS query_age,
       pg_blocking_pids(pid) AS blockers,
       left(query, 90) AS q
FROM pg_stat_activity
WHERE datname LIKE 'alpha%' AND pid <> pg_backend_pid()
  AND (state <> 'idle' OR age(clock_timestamp(), state_change) > interval '60 seconds')
ORDER BY state_age DESC
LIMIT 30
"""

Q2 = """
SELECT count(*) FILTER (WHERE state='idle in transaction') AS idle_tx,
       count(*) FILTER (WHERE state='active') AS active,
       count(*) FILTER (WHERE state='idle') AS idle,
       max(age(clock_timestamp(), state_change)) FILTER (WHERE state='idle in transaction') AS max_idle_tx_age
FROM pg_stat_activity WHERE datname LIKE 'alpha%'
"""

with psycopg.connect(read_env_dsn()) as c:
    with c.cursor() as cur:
        cur.execute(Q2)
        print("会话统计（idle_tx / active / idle / 最老 idle_tx）:", cur.fetchone())
        cur.execute(Q1)
        rows = cur.fetchall()
        print(f"\n可疑会话 {len(rows)} 条：")
        for r in rows:
            print(f"  pid={r[0]:<7} {r[1]:<22} app={r[2]:<20} state_age={r[3]} "
                  f"query_age={r[4]} blockers={r[5]} q={r[6]!r}")
