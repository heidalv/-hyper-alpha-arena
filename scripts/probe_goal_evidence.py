# -*- coding: utf-8 -*-
"""目标硬约束证据：当前腿速（≥60 腿/h）+ #2 试跑状态 + 超时硬上限生效。只读。"""
import sys
import json
import pathlib
import datetime as dt
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
now = dt.datetime.now(dt.timezone.utc)

# 1) 近 3h 腿数与腿速
cur.execute("""
    SELECT COUNT(*), MIN(ts), MAX(ts)
    FROM lane_ledger
    WHERE ts >= now() - interval '3 hours'
""")
n, mn, mx = cur.fetchone()
hours = (mx - mn).total_seconds() / 3600.0 if n else 0
print(f"近 3h 腿数: {n}（{n / hours:.0f} 腿/h）" if hours > 0 else "近 3h 无腿")

# 2) 超时硬上限（#19 h411）生效性：>300s 持仓
cur.execute("""
    SELECT COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND (meta_json->>'exit_path') = 'timeout_hard_taker'
      AND ts >= now() - interval '6 hours'
""")
print("近 6h timeout_hard_taker 出场:", cur.fetchone()[0])

# 3) #2 试跑当前状态（h356）
cur.execute("""
    SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'
""")
m = cur.fetchone()[0]
trial = {k: v for k, v in m.items() if "trial" in k or "h3" in k}
print("#2 试跑相关键:", json.dumps(trial, ensure_ascii=False)[:600])

# 4) 活跃持仓年龄
cur.execute("""
    SELECT symbol, meta_json->>'side' AS side, COUNT(*),
           MAX(EXTRACT(EPOCH FROM (now()-ts))/60)::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '30 minutes'
    GROUP BY symbol, meta_json->>'side' ORDER BY 4 DESC LIMIT 5
""")
print("近 30min 按币/向 腿数与最大龄(分):")
for r in cur.fetchall():
    print("  ", r)
