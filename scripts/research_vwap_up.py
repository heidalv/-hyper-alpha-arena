# -*- coding: utf-8 -*-
"""研究③：vwap_revert_up 剩余亏损的精确解剖（8h，103 腿，−$1.91）。只读。"""
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

print("== vwap_revert_up 组：侧 × 出场路径（12:56 起）==")
cur.execute("""
    SELECT meta_json->>'side', COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'),
           COUNT(*), ROUND(AVG(net_bp)::numeric,1),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_reason' = 'vwap_revert_up'
    GROUP BY 1,2 ORDER BY 4 DESC
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<6} {str(r[1]):<24} n={r[2]:>4} 均={r[3]:>+7}bp Σ=${r[4]:>+9}")

print("\n== 该组在 12:56 前（修复前）对照 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(AVG(net_bp)::numeric,1),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T04:56:00+00'::timestamptz
      AND ts < '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_reason' = 'vwap_revert_up'
""")
r = cur.fetchone()
print(f"  24h 前对照: n={r[0]} 均={r[1]}bp Σ=${r[2]}")

print("\n== vwap_flow_block 是否在拦（skip 增量近况）==")
import json
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
sk = j.get("skip_counts") or {}
for k in sorted(sk):
    if "vwap_flow" in k or "flow_away" in k:
        print(f"  {k}: {sk[k]}")
print("  （若 vwap_flow_away 计数为 0 = 闸从未触发）")
