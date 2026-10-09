# -*- coding: utf-8 -*-
"""硬上限真验证：exit_path='' 的未平腿年龄分布（这才是持仓年龄）。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT symbol, meta_json->>'side', EXTRACT(EPOCH FROM (now()-ts))/60::int,
           (meta_json->>'quote_ts') IS NOT NULL
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '40 minutes'
      AND (meta_json->>'exit_path' IS NULL OR meta_json->>'exit_path' = '')
    ORDER BY 3 DESC
""")
rows = cur.fetchall()
print(f"未平腿（exit_path 空）共 {len(rows)} 条：")
for r in rows[:15]:
    print("  ", r)
over = [r for r in rows if r[2] > 5]
print("超过 5 分钟的未平腿:", len(over))
