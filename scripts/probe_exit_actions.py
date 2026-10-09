# -*- coding: utf-8 -*-
"""exit_path 空的腿按龄分布 + 这些腿的 exit_action/exit_reason 构成。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT EXTRACT(EPOCH FROM (now()-ts))/60::int AS age_min, COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '2 hours'
      AND (meta_json->>'exit_path' IS NULL OR meta_json->>'exit_path' = '')
    GROUP BY 1 ORDER BY 1 DESC
""")
rows = cur.fetchall()
print("== exit_path 空的腿（近 2h）按龄分布：龄(分) → n ==")
for age, n in rows:
    bar = "#" * min(n, 60)
    print(f"  {age:>3}  {n:>4}  {bar}")

cur.execute("""
    SELECT COALESCE(meta_json->>'exit_action','(null)'), COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '2 hours'
      AND (meta_json->>'exit_path' IS NULL OR meta_json->>'exit_path' = '')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 8
""")
print("\n== 空 exit_path 腿的 exit_action ==")
for r in cur.fetchall():
    print("  ", r)
