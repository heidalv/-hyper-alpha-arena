# -*- coding: utf-8 -*-
"""根因审计 ②：参数漂移（ops_changes）+ 止损腿亏损分布 + 止损时刻波动环境。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== ops_changes 中涉及关键参数的全部变更 ==")
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
oc = m.get("ops_changes") or []
for e in oc:
    if not isinstance(e, dict):
        continue
    f = e.get("field") or e.get("action") or ""
    s = json.dumps(e, ensure_ascii=False)
    if any(x in s for x in ("stop", "spread_mult", "vwap", "trend", "grace", "vol", "timeout")):
        print(f"  {s[:240]}")

print("\n== 近 12h 止损腿亏损分布 ==")
cur.execute("""
    SELECT MIN(net_bp)::int, MAX(net_bp)::int,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_bp)::int AS med,
           COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
""")
mn, mx, med, n = cur.fetchone()
print(f"  n={n}  min={mn}bp  median={med}bp  max={mx}bp")
cur.execute("""
    SELECT width_bucket(net_bp, -100, 0, 5) AS b, COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    GROUP BY 1 ORDER BY 1
""")
labels = {-100: "<-100", -80: "-100~-80", -60: "-80~-60", -40: "-60~-40", -20: "-40~-20", 0: "-20~0"}
print("  分布:")
for b, n2 in cur.fetchall():
    edge = -100 + b * 20
    print(f"    {edge}~{edge+20}bp: {n2}")

print("\n== 止损腿的时刻与波动环境（mid_hist 不可得，用该币该分钟 book 波动近似）==")
cur.execute("""
    SELECT symbol, COUNT(*), ROUND(AVG(net_bp)::numeric,1),
           ROUND(AVG((meta_json->>'qty')::float8 * (meta_json->>'fill_px')::float8)::numeric,0)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<6} n={r[1]:>3} mean_net={r[2]:>+6}bp mean_notional={r[3]}")

print("\n== 硬上限腿分布 ==")
cur.execute("""
    SELECT MIN(net_bp)::int, MAX(net_bp)::int,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_bp)::int AS med, COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
      AND meta_json->>'exit_path' = 'timeout_hard_taker'
""")
print("  ", cur.fetchone())

print("\n== 逐小时 stop+hard 强平笔数（趋势段放血节奏）==")
cur.execute("""
    SELECT date_trunc('hour', ts) AS h, COUNT(*),
           ROUND(AVG(net_bp)::numeric,1)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
      AND meta_json->>'exit_path' IN ('stop_loss_taker','timeout_hard_taker')
    GROUP BY 1 ORDER BY 1
""")
for r in cur.fetchall():
    print(f"  {r[0]:%H:%M}  n={r[1]:>3}  mean={r[2]:>+7}bp")
