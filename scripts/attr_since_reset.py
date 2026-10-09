# -*- coding: utf-8 -*-
"""重置后（09:35 起）亏损归因：逐出场路径 USD + 逐 15min 轨迹。只读。"""
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
SINCE = "2026-09-28T01:35:00+00"   # 09:35 本地

print("== 重置后按出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)') AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,1),
           ROUND(SUM(net_bp*notional/1e4)::numeric,4)
    FROM lane_ledger
    WHERE event='fill' AND ts >= %s::timestamptz
    GROUP BY 1 ORDER BY 4
""", (SINCE,))
tot = 0.0
for r in cur.fetchall():
    tot += float(r[3] or 0)
    print(f"  {r[0]:<22} n={r[1]:>4} 均={r[2]:>+7}bp Σ=${r[3]:>+9}")
print(f"  合计 = ${tot:+.4f}")

print("\n== 逐 15min 轨迹 ==")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int/15)*interval '15 min' AS b,
           COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4)
    FROM lane_ledger WHERE event='fill' AND ts >= %s::timestamptz
    GROUP BY 1 ORDER BY 1
""", (SINCE,))
run = 0.0
for b, n, s in cur.fetchall():
    run += float(s or 0)
    print(f"  {b:%H:%M}  n={n:>4}  Σ=${s:>+8}  累计=${run:>+9}")

print("\n== jump_exit 腿明细（重置后）==")
cur.execute("""
    SELECT to_char(ts,'HH24:MI'), symbol, meta_json->>'side',
           ROUND(net_bp::numeric,1), ROUND((net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= %s::timestamptz
      AND meta_json->>'exit_path' = 'jump_exit_taker'
    ORDER BY ts
""", (SINCE,))
rows = cur.fetchall()
for r in rows:
    print(f"  {r[0]} {r[1]:<6} {r[2]:<5} {r[3]:>+7}bp  ${r[4]:>+7}")
print(f"  共 {len(rows)} 笔")
