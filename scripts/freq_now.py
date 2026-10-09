# -*- coding: utf-8 -*-
"""精确腿速：近 60/30min 腿数 + 逐 10min 分桶。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE ts >= now() - interval '60 minutes') AS h1,
           COUNT(*) FILTER (WHERE ts >= now() - interval '30 minutes') AS h30,
           COUNT(*) FILTER (WHERE ts >= now() - interval '10 minutes') AS m10
    FROM lane_ledger WHERE event='fill'
""")
h1, h30, m10 = cur.fetchone()
print(f"近 60min={h1} 腿（{h1}/h）  近 30min={h30}（{h30*2}/h）  近 10min={m10}（{m10*6}/h）")

print("\n近 1h 逐 10min：")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int/10)*interval '10 min' AS b,
           COUNT(*)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '60 minutes'
    GROUP BY 1 ORDER BY 1
""")
for b, n in cur.fetchall():
    bar = "#" * min(n, 40)
    print(f"  {b:%H:%M}  {n:>3}  {bar}")
