# -*- coding: utf-8 -*-
"""历史下午时段腿速基准：昨天/前天 13:00-16:00 的腿速（判断现在是市场还是新闸）。只读。"""
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
    SELECT date_trunc('day', ts) AS d,
           EXTRACT(HOUR FROM ts)::int AS h,
           COUNT(*) / 1.0 AS legs
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '3 days'
      AND EXTRACT(HOUR FROM ts) BETWEEN 12 AND 16
    GROUP BY 1,2 ORDER BY 1,2
""")
by_day = {}
for d, h, n in cur.fetchall():
    by_day.setdefault(str(d)[:10], {})[int(h)] = int(n)
print("== 历史 12-16 点逐小时腿数（今天 vs 昨天 vs 前天）==")
print(f"  {'小时':<6} " + " ".join(f"{day:<12}" for day in sorted(by_day)))
for h in range(12, 17):
    row = []
    for day in sorted(by_day):
        row.append(str(by_day[day].get(h, 0)))
    print(f"  {h:>2}:00  " + " ".join(f"{x:>12}" for x in row))
