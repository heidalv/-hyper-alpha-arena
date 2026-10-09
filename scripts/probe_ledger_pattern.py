# -*- coding: utf-8 -*-
"""账本记录模式：最近 12 行全字段 + 按 exit_path 分组的近 90min 统计。只读。"""
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
    SELECT ts, symbol, event, meta_json FROM lane_ledger
    WHERE ts >= now() - interval '8 minutes'
    ORDER BY ts DESC LIMIT 12
""")
print("== 最近 12 行 ==")
for ts, sym, ev, m in cur.fetchall():
    mj = m or {}
    print(f"{ts:%H:%M:%S} {sym:<6} {ev:<8} side={mj.get('side')} "
          f"exit={mj.get('exit_path','')!r} exit_a={mj.get('exit_action','')} "
          f"qty={mj.get('qty')} fill={mj.get('fill_px')}")

cur.execute("""
    SELECT COALESCE(meta_json->>'exit_path','(null)') AS ep, COUNT(*),
           MIN(EXTRACT(EPOCH FROM (now()-ts))/60)::int,
           MAX(EXTRACT(EPOCH FROM (now()-ts))/60)::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '90 minutes'
    GROUP BY 1 ORDER BY 2 DESC
""")
print("\n== 近 90min 按 exit_path 分组（n, 最小龄, 最大龄 min）==")
for r in cur.fetchall():
    print("  ", r)

cur.execute("""
    SELECT event, COUNT(*) FROM lane_ledger
    WHERE ts >= now() - interval '90 minutes'
    GROUP BY 1 ORDER BY 2 DESC
""")
print("\n== 近 90min 按 event 分组 ==")
for r in cur.fetchall():
    print("  ", r)
