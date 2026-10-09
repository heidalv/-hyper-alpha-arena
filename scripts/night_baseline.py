# -*- coding: utf-8 -*-
"""夜间腿速基准：22:00-01:00 逐小时历史对照（判断 40/h 是市场还是闸门）。只读。"""
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
    SELECT date_trunc('day', ts)::date AS d, EXTRACT(HOUR FROM ts)::int AS h, COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 days'
      AND EXTRACT(HOUR FROM ts) IN (22, 23, 0, 1)
    GROUP BY 1,2 ORDER BY 1,2
""")
by = {}
for d, h, n in cur.fetchall():
    by.setdefault(str(d), {})[int(h)] = int(n)
days = sorted(by)
print("夜间逐小时腿数（22/23/00/01 时）")
print(f"  {'日期':<12}" + "".join(f"{h:>6}:00" for h in (22, 23, 0, 1)))
for d in days:
    print(f"  {d:<12}" + "".join(f"{by[d].get(h, 0):>10}" for h in (22, 23, 0, 1)))

print("\n近 6h 逐小时（今天）：")
cur.execute("""
    SELECT date_trunc('hour', ts) AS h, COUNT(*)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '6 hours'
    GROUP BY 1 ORDER BY 1
""")
for h, n in cur.fetchall():
    print(f"  {h:%H:%M}  {n:>4}")
