# -*- coding: utf-8 -*-
"""亏损深挖：USD 口径逐 2h + 止损腿细节 + 硬上限腿细节。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== USD 逐 2h（points_usd 合计）==")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int / 120) * interval '2 hour' AS bucket,
           COUNT(*), ROUND(SUM(points_usd)::numeric, 2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
    GROUP BY 1 ORDER BY 1
""")
for b, n, s in cur.fetchall():
    print(f"  {b:%H:%M}  n={n:>4}  Σusd={s:>+9}")

print("\n== 近 4h 止损腿（stop_loss_taker）细节 ==")
cur.execute("""
    SELECT symbol, meta_json->>'side', ROUND(net_bp::numeric,1),
           ROUND(points_usd::numeric,2),
           EXTRACT(EPOCH FROM (ts - to_timestamp((meta_json->>'opened_ts')::float8)))/60::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    ORDER BY ts
""")
rows = cur.fetchall()
for r in rows:
    print(f"  {r[0]:<6} {r[1]:<5} net={r[2]:>+7}bp usd={r[3]:>+8} age_min={r[4]}")
print(f"  （共 {len(rows)} 笔）")

print("\n== 近 4h 硬上限腿（timeout_hard_taker）age 分布 ==")
cur.execute("""
    SELECT EXTRACT(EPOCH FROM (ts - to_timestamp((meta_json->>'opened_ts')::float8)))/60::int AS age,
           COUNT(*), ROUND(AVG(net_bp)::numeric,1)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 hours'
      AND meta_json->>'exit_path' = 'timeout_hard_taker'
      AND meta_json->>'opened_ts' IS NOT NULL
    GROUP BY 1 ORDER BY 1
""")
for r in cur.fetchall():
    print(f"  age={r[0]:>3}min  n={r[1]:>3}  mean_net={r[2]:>+7}bp")

print("\n== quote_round 里带 exit_reason 的（toxic 出场？）==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_reason',''),'(none)') AS er, COUNT(*),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 hours'
      AND (meta_json->>'exit_path' IS NULL OR meta_json->>'exit_path'='')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 8
""")
for r in cur.fetchall():
    print(f"  {r[0]:<20} n={r[1]:>5}  mean_net={r[2]:>+7}bp")
