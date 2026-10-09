# -*- coding: utf-8 -*-
"""14:00 中期检查：12:56 起逐 15min 轨迹 + 出血路径收窄对照。只读。"""
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

print("== 12:56 起逐 15min ==")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int/15)*interval '15 min' AS b,
           COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
    GROUP BY 1 ORDER BY 1
""")
run = 0.0
for b, n, s in cur.fetchall():
    run += float(s or 0)
    print(f"  {b:%H:%M}  n={n:>4}  Σ=${s:>+8}  累计=${run:>+9}")

print("\n== 12:56 起出血路径对照 ==")
cur.execute("""
    SELECT meta_json->>'exit_reason' AS er, COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_reason' IN ('vwap_revert_up','trend_down',
                                        'vwap_revert_down','trend_up','sudden_move')
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<22} n={r[1]:>4} Σ=${r[2]:>+8}")

print("\n== 强平路径对照（修复前 vs 修复后）==")
cur.execute("""
    SELECT meta_json->>'exit_path' AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,1)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_path' LIKE '%taker%'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<24} n={r[1]:>4} 均={r[2]:>+6}bp")
