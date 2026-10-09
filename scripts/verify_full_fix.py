# -*- coding: utf-8 -*-
"""全面修复部署核实：worker limits 快照 vs 目标值 + 试跑元信息 + 近 30min 腿况。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TARGET = {
    "stop_maker_grace_sec": 0.0, "jump_exit_bp": 0.0, "stop_loss_vol_min": 0.0,
    "max_one_side_seconds": 45.0, "timeout_hard_taker_sec": 300.0,
    "stop_loss_bp": 40.0, "vwap_flow_block": 0.3, "pullback_flow_block": 0.3,
    "trail_lock_bp": 20.0, "post_stop_decay": 0.5,
    "p1_hold_sec": 60.0, "p45_hold_sec": 300.0, "p3_spike_gate": 0.3,
}

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
lim = j.get("limits") or {}
p = j.get("params") or {}
print("== worker 实测（limits+params）==")
ok_all = True
for k, want in TARGET.items():
    v = lim.get(k, p.get(k))
    v = float(v) if v is not None else None
    good = v is not None and abs(v - float(want)) < 1e-6
    ok_all &= good
    print(f"  [{'OK' if good else 'FAIL'}] {k:<26} = {v}（目标 {want}）")
print("worker ok:", j.get("ok"), "| equity:", j.get("equity"), "| 全部达标:", ok_all)

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("\n== 试跑元信息（7 项新部署）==")
for k in ("h433_trial", "h434_trial", "h435_trial", "h436_trial",
          "h437_trial", "h438_trial", "h439_trial"):
    t = m.get(k) or {}
    print(f"  {k}: started={str(t.get('started_at'))[:16]} judge={str(t.get('judge_at'))[:16]}"
          f" to={t.get('to')}")

print("\n== 近 30min 腿况 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '30 minutes'
""")
r = cur.fetchone()
print(f"  {r[0]} 腿  Σ=${r[1]}  均={r[2]}bp")
