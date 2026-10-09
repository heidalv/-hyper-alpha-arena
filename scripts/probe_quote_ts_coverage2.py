# -*- coding: utf-8 -*-
"""闭环验证准备：账本里 quote_ts 覆盖率（能否把已成交腿归因到入场状态）。只读。"""
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
    SELECT COUNT(*) FILTER (WHERE (meta_json->>'quote_ts') ~ '^[0-9.]+$'
                             AND (meta_json->>'quote_ts')::float8 > 0) AS with_q,
           COUNT(*)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '12 hours'
""")
wq, tot = cur.fetchone()
print(f"近 12h：{tot} 腿，其中带 quote_ts {wq} 条（{wq/max(tot,1)*100:.0f}%）")
cur.execute("""
    SELECT MIN(ts), MAX(ts) FROM lane_ledger
    WHERE event='fill' AND (meta_json->>'quote_ts') ~ '^[0-9.]+$'
      AND (meta_json->>'quote_ts')::float8 > 0
""")
print("带 quote_ts 的腿时间跨度:", cur.fetchone())
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE (meta_json->>'exit_path') IS NULL
                            OR (meta_json->>'exit_path') = ''), COUNT(*)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '12 hours'
""")
print("近 12h 往返腿/总腿:", cur.fetchone())
