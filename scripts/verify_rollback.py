# -*- coding: utf-8 -*-
"""回滚核实：jump_exit=0 / stop_loss_vol_min=0 + 运行时状态 + 近 15min 腿况。只读。"""
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
p = m.get("params") or {}
print("== 当前关键参数 ==")
for k in ("stop_maker_grace_sec", "jump_exit_bp", "stop_loss_vol_min",
          "max_one_side_seconds", "timeout_hard_taker_sec",
          "exit_skew_k", "vol_spread_k", "stop_loss_bp"):
    print(f"  {k} = {p.get(k)}")

print("\n== 近 15min 出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)') AS ep, COUNT(*),
           ROUND(AVG(net_bp)::numeric,1),
           ROUND(SUM(net_bp*notional/1e4)::numeric,4)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '15 minutes'
    GROUP BY 1 ORDER BY 4
""")
for r in cur.fetchall():
    print(f"  {r[0]:<22} n={r[1]:>4} 均={r[2]:>+7}bp Σ=${r[3]:>+9}")

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
print("\nworker:", "ok" if j.get("ok") else j.get("reason"), "| equity:", j.get("equity"))
