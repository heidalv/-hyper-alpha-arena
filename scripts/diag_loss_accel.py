# -*- coding: utf-8 -*-
"""亏损加速诊断：逐小时盈亏轨迹 + 近期腿结构 + 判决链状态。只读。"""
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

print("== 1) 逐 2h 净盈亏（net_bp 合计, 腿数, 均值bp）==")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int / 120) * interval '2 hour' AS bucket,
           COUNT(*), SUM(net_bp)::int, ROUND(AVG(net_bp)::numeric, 2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '18 hours'
    GROUP BY 1 ORDER BY 1
""")
for b, n, s, m in cur.fetchall():
    print(f"  {b:%m-%d %H:%M}  n={n:>5}  Σnet={s:>+8}bp  mean={m:>+7}bp")

print("\n== 2) 近 4h 逐币 Σnet_bp / 腿数 ==")
cur.execute("""
    SELECT symbol, COUNT(*), SUM(net_bp)::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 hours'
    GROUP BY 1 ORDER BY 3
""")
for r in cur.fetchall():
    print(f"  {r[0]:<6} n={r[1]:>5}  Σnet={r[2]:>+8}bp")

print("\n== 3) 近 4h 出场路径分布 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(''quote_round'')') AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '4 hours'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<22} n={r[1]:>5}  mean_net={r[2]:>+7}bp")

print("\n== 4) 判决链状态 ==")
cur.execute("""
    SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'
""")
m = cur.fetchone()[0]
for k in sorted(m):
    if "trial" in k or "h356" in k or "h411" in k or "h410" in k:
        v = m[k]
        s = json.dumps(v, ensure_ascii=False)
        print(f"  {k}: {s[:200]}")
