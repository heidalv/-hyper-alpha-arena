# -*- coding: utf-8 -*-
"""14:30 中检：腿速 + 12:56 起累计盈亏 + 出血路径收窄。只读。"""
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

cur.execute("""
    SELECT COUNT(*) FILTER (WHERE ts >= now() - interval '30 minutes') AS n30,
           COUNT(*) FILTER (WHERE ts >= '2026-09-28T04:56:00+00'::timestamptz) AS nall,
           ROUND(SUM(net_bp*notional/1e4) FILTER (WHERE ts >= '2026-09-28T04:56:00+00'::timestamptz)::numeric,3)
    FROM lane_ledger WHERE event='fill'
""")
n30, nall, usd = cur.fetchone()
print(f"近 30min: {n30} 腿（{n30*2}/h） | 12:56 起共 {nall} 腿 Σ=${usd}")

print("\n== 12:56 起出血路径 ==")
cur.execute("""
    SELECT meta_json->>'exit_reason' AS er, COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_reason' IN ('vwap_revert_up','trend_down','vwap_revert_down','trend_up')
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<20} n={r[1]:>4} Σ=${r[2]:>+8}")

print("\n== 12:56 起全部出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
           ROUND(AVG(net_bp)::numeric,1), ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
    GROUP BY 1 ORDER BY 4
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<24} n={r[1]:>4} 均={r[2]:>+6}bp Σ=${r[3]:>+8}")

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
print("\nworker ok:", j.get("ok"), "| equity:", j.get("equity"))
