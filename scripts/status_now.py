# -*- coding: utf-8 -*-
"""全修复上线后运行状态总览：时间/腿速/盈亏/出场路径/止损形态/worker 健康。只读。"""
import sys
import json
import pathlib
import datetime as dt
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

now = dt.datetime.now()
print(f"现在 {now:%H:%M:%S}")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("\n== 逐小时盈亏（近 6h）==")
cur.execute("""
    SELECT date_trunc('hour', ts) AS h, COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,2),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '6 hours'
    GROUP BY 1 ORDER BY 1
""")
for r in cur.fetchall():
    print(f"  {r[0]:%H:%M}  n={r[1]:>4}  usd={r[2]:>+8}  mean={r[3]:>+6}bp")

print("\n== 部署后（02:40 起）出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)') AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,2), ROUND(SUM(net_bp*notional/1e4)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T18:40:00+00'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<24} n={r[1]:>5} mean={r[2]:>+7}bp usd={r[3]:>+8}")

print("\n== 止损腿明细（部署后）==")
cur.execute("""
    SELECT symbol, meta_json->>'side', ROUND(net_bp::numeric,1), to_char(ts,'HH24:MI')
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T18:40:00+00'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    ORDER BY ts
""")
rows = cur.fetchall()
for r in rows:
    print(f"  {r[0]:<6} {r[1]:<5} net={r[2]:>+7}bp @{r[3]}")
print(f"  共 {len(rows)} 笔（部署前 12h 是 30 笔）")

print("\n== worker 状态 ==")
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
print("  ok:", j.get("ok"), "| equity:", j.get("equity"),
      "| symbols:", len(j.get("symbols") or []))
p = j.get("params") or {}
print("  v2 参数:", {k: p.get(k) for k in ("exit_skew_k", "vol_spread_k") if k in p})

print("\n== 近 30min 腿速 ==")
cur.execute("""
    SELECT COUNT(*) FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '30 minutes'
""")
n = cur.fetchone()[0]
print(f"  {n} 腿 / 30min = {n*2} 腿/h")
