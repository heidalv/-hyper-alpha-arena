# -*- coding: utf-8 -*-
"""13:40 验证快照：五项判定结果 + 新闸触发计数 + 近 1h 盈亏。只读。"""
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
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]

print("== 13:35 五项判定结果 ==")
for k in ("h392_trial", "h429_trial", "h425_trial", "h426_trial", "h427_trial"):
    t = m.get(k) or {}
    print(f"  {k}: verdict={t.get('verdict','(未判)')} {str(t.get('why',''))[:80]}")

print("\n== worker skip_counts（新闸是否真的在触发）==")
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
sk = j.get("skip_counts") or {}
for k in sorted(sk):
    if any(x in k for x in ("vwap_flow", "pullback", "flow_away", "spike", "trail")):
        print(f"  {k}: {sk[k]}")
print("  （全部 skip 键:", ", ".join(f"{k}={v}" for k, v in sorted(sk.items(), key=lambda x: -x[1])[:8]), "…）")

print("\n== 近 1h 盈亏 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '1 hour'
""")
r = cur.fetchone()
print(f"  {r[0]} 腿  Σ=${r[1]}  均={r[2]}bp")

print("\n== 近 1h 出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
           ROUND(AVG(net_bp)::numeric,1), ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '1 hour'
    GROUP BY 1 ORDER BY 4
""")
for r in cur.fetchall():
    print(f"  {r[0]:<22} n={r[1]:>4} 均={r[2]:>+6}bp Σ=${r[3]:>+8}")
