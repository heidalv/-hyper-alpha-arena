# -*- coding: utf-8 -*-
"""16:45 收敛验证快照：部署后累计盈亏/出场路径/新闸计数/止损形态。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== 逐小时盈亏（近 8h）==")
cur.execute("""
    SELECT date_trunc('hour', ts) AS h, COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '8 hours'
    GROUP BY 1 ORDER BY 1
""")
tot = 0.0
for r in cur.fetchall():
    tot += float(r[2] or 0)
    print(f"  {r[0]:%H:%M}  n={r[1]:>4}  Σ=${r[2]:>+8}")

print("\n== 12:56 全面修复起累计 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,3),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
""")
r = cur.fetchone()
print(f"  {r[0]} 腿  Σ=${r[1]}  均={r[2]}bp")

print("\n== 12:56 起出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
           ROUND(AVG(net_bp)::numeric,1), ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
    GROUP BY 1 ORDER BY 4
""")
for r in cur.fetchall():
    print(f"  {r[0]:<24} n={r[1]:>4} 均={r[2]:>+6}bp Σ=${r[3]:>+8}")

print("\n== 止损腿（12:56 起，对照修复前 −62bp）==")
cur.execute("""
    SELECT COUNT(*), ROUND(AVG(net_bp)::numeric,1),
           ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_bp)::numeric,1), MIN(net_bp)::int
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_path' = 'stop_loss_taker'
""")
print("  ", cur.fetchone())

print("\n== 新闸触发计数 ==")
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
sk = j.get("skip_counts") or {}
for k in sorted(sk):
    if any(x in k for x in ("flow_away", "spike", "pullback")):
        print(f"  {k}: {sk[k]}")
print("  worker ok:", j.get("ok"), "| equity:", j.get("equity"))
