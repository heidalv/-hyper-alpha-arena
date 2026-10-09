# -*- coding: utf-8 -*-
"""T1-T5 部署后首验：近 1h 止损腿均值/尾部、jump_exit 出现、频率与盈亏。只读。"""
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

print("== 部署后近 1h vs 部署前 12h 对照 ==")
cur.execute("""
    SELECT CASE WHEN ts >= '2026-09-27T17:40:00+00' THEN '部署后' ELSE '部署前12h' END AS era,
           COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,2),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '13 hours'
    GROUP BY 1
""")
for r in cur.fetchall():
    print(f"  {r[0]:<10} n={r[1]:>5} usd={r[2]:>+8} mean={r[3]:>+7}bp")

print("\n== 止损腿对照（均值/中位/最惨）==")
cur.execute("""
    SELECT CASE WHEN ts >= '2026-09-27T17:40:00+00' THEN '部署后' ELSE '部署前12h' END AS era,
           COUNT(*),
           ROUND(AVG(net_bp)::numeric,1),
           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_bp)::numeric,1),
           MIN(net_bp)::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '13 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    GROUP BY 1
""")
for r in cur.fetchall():
    print(f"  {r[0]:<10} n={r[1]:>3} mean={r[2]:>+7}bp median={r[3]:>+7}bp min={r[4]}bp")

print("\n== 部署后新出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)') AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T17:40:00+00'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<24} n={r[1]:>5} mean={r[2]:>+7}bp")

print("\n== 部署后 skip/sudden_move 痕迹（exit_reason）==")
cur.execute("""
    SELECT meta_json->>'exit_reason' AS er, COUNT(*)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T17:40:00+00'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 8
""")
for r in cur.fetchall():
    print(f"  {r[0]:<24} n={r[1]}")
