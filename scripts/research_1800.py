# -*- coding: utf-8 -*-
"""18:00 小时亏损归因（−$3.64，修复后最差小时）+ 正收益止损腿复核。只读。"""
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

print("== 18:00-19:00 出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
           ROUND(AVG(net_bp)::numeric,1), ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-28T10:00:00+00'::timestamptz
      AND ts < '2026-09-28T11:00:00+00'::timestamptz
    GROUP BY 1 ORDER BY 4
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<24} n={r[1]:>4} 均={r[2]:>+6}bp Σ=${r[3]:>+8}")

print("\n== 18:00 小时逐币 ==")
cur.execute("""
    SELECT symbol, COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-28T10:00:00+00'::timestamptz
      AND ts < '2026-09-28T11:00:00+00'::timestamptz
    GROUP BY 1 ORDER BY 3
""")
for r in cur.fetchall():
    print(f"  {r[0]:<6} n={r[1]:>4} Σ=${r[2]:>+8}")

print("\n== 正收益止损腿复核（12:56 起 net_bp>0 的 stop 腿）==")
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', net_bp,
           (meta_json->>'fill_px')::float8, (meta_json->>'mid_px')::float8
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_path' = 'stop_loss_taker' AND net_bp > 0
    ORDER BY ts
""")
for r in cur.fetchall():
    print(f"  {r[0]:%H:%M:%S} {r[1]:<6} {r[2]:<5} net={float(r[3]):+.1f}bp "
          f"fill={r[4]} mid={r[5]} 差={(float(r[4])-float(r[5]))/float(r[5])*1e4:+.1f}bp")
