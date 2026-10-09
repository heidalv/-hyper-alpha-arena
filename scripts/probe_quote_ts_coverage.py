# -*- coding: utf-8 -*-
"""P4 前置探针：lane_ledger 中 quote_ts 的覆盖度 + 可用字段。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT MIN(to_timestamp((meta_json->>'quote_ts')::float8)),
           MAX(to_timestamp((meta_json->>'quote_ts')::float8)),
           COUNT(*) FILTER (WHERE (meta_json->>'quote_ts') ~ '^[0-9.]+$'
                            AND (meta_json->>'quote_ts')::float8 > 0),
           COUNT(*)
    FROM lane_ledger
""")
r = cur.fetchone()
print("quote_ts range:", r[0], "->", r[1])
print("with quote_ts:", r[2], "/", r[3])

cur.execute("""
    SELECT meta_json FROM lane_ledger
    WHERE (meta_json->>'quote_ts') ~ '^[0-9.]+$' AND (meta_json->>'quote_ts')::float8 > 0
    ORDER BY ts DESC LIMIT 2
""")
for row in cur.fetchall():
    print(json.dumps(row[0], ensure_ascii=False, indent=1))
