# -*- coding: utf-8 -*-
"""根因审计 ①：meta 字段全貌 + 逐侧/逐路径盈亏 + 活跃阻塞计数。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== 近 12h 按 side 盈亏（买卖两侧都亏 = 市场漂移，非策略）==")
cur.execute("""
    SELECT meta_json->>'side', COUNT(*), SUM(net_bp)::int,
           ROUND(AVG(net_bp)::numeric,2), ROUND(SUM(net_bp*notional/1e4)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<6} n={r[1]:>5} Σnet={r[2]:>+8}bp mean={r[3]:>+7}bp usd={r[4]:>+8}")

print("\n== 近 12h 按 exit_path（强平路径）==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)') AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,2), SUM(net_bp)::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<22} n={r[1]:>5} mean={r[2]:>+7}bp Σ={r[3]:>+8}bp")

print("\n== 近 12h 按 exit_reason（方向/闸因）==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_reason',''),'(none)') AS er, COUNT(*),
           ROUND(AVG(net_bp)::numeric,2), SUM(net_bp)::int
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '12 hours'
    GROUP BY 1 ORDER BY 3 DESC LIMIT 15
""")
for r in cur.fetchall():
    print(f"  {r[0]:<24} n={r[1]:>5} mean={r[2]:>+7}bp Σ={r[3]:>+8}bp")

print("\n== meta_json 全字段集合（近 12h 出现过的键）==")
cur.execute("""
    SELECT DISTINCT jsonb_object_keys(meta_json) AS k
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '12 hours'
    ORDER BY 1
""")
print("  ", [r[0] for r in cur.fetchall()])

print("\n== lane_registry 当前 params（全部）==")
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("  params:", json.dumps(m.get("params", {}), ensure_ascii=False))
print("\n== 心跳/阻塞计数（registry 里的 skip/block 类键）==")
for k in sorted(m):
    if any(x in k for x in ("skip", "block", "count", "telemetry")):
        print(f"  {k}: {json.dumps(m[k], ensure_ascii=False)[:200]}")
