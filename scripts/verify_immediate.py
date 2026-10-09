# -*- coding: utf-8 -*-
"""12:56 部署后即时检查：新闸触发 + 腿况 + 出场路径。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
sk = j.get("skip_counts") or {}
print("== skip_counts（新闸相关）==")
for k in sorted(sk):
    if any(x in k for x in ("vwap_flow", "pullback", "flow_away", "spike")):
        print(f"  {k}: {sk[k]}")
new_skips = sum(v for k, v in sk.items() if "flow_away" in k)
print(f"  vwap_flow_away 类合计触发: {new_skips}")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
print("\n== 12:56 起腿况 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
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
    print(f"  {r[0]:<22} n={r[1]:>4} 均={r[2]:>+6}bp Σ=${r[3]:>+8}")

print("\n== 12:56 起 exit_reason 前 8 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_reason',''),'(none)'), COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
    GROUP BY 1 ORDER BY 2 DESC LIMIT 8
""")
for r in cur.fetchall():
    print(f"  {r[0]:<26} n={r[1]:>5} Σ=${r[2]:>+8}")
