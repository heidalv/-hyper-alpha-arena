# -*- coding: utf-8 -*-
"""总检：判定链结果 + 配置清点 + 频率/盈亏现状。只读。"""
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

print("现在:", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]

print("\n== 试跑判定结果（近期）==")
rows = []
for k, v in m.items():
    if k.endswith("_trial") and isinstance(v, dict):
        rows.append((str(v.get("started_at") or "")[:16], k, v.get("verdict") or "(未判)",
                     str(v.get("why") or "")[:40]))
for st, k, vd, why in sorted(rows):
    print(f"  {st}  {k:<14} {vd:<14}{why}")

print("\n== 当前在线参数（清点）==")
p = m.get("params") or {}
key = ("ofi_confirm_threshold", "trend_only_q", "trend_only_bp", "compound_ratio",
       "stop_maker_grace_sec", "timeout_hard_taker_sec", "max_one_side_seconds",
       "stop_loss_bp", "stop_ref_last_leg", "trail_lock_bp", "post_stop_decay",
       "p1_hold_sec", "p45_hold_sec", "ofi_flatten_threshold", "vwap_revert_bp",
       "vwap_flow_block", "exit_skew_k", "vol_spread_k", "sudden_move_cooldown_sec",
       "jump_exit_bp", "stop_loss_vol_min", "k_trend", "p3_spike_gate",
       "pullback_flow_block")
for k in key:
    print(f"  {k:<26} {p.get(k)}")

print("\n== 频率与盈亏 ==")
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE ts >= now() - interval '30 minutes'),
           COUNT(*) FILTER (WHERE ts >= now() - interval '3 hours'),
           ROUND(SUM(net_bp*notional/1e4) FILTER (WHERE ts >= now() - interval '3 hours')::numeric,3)
    FROM lane_ledger WHERE event='fill'
""")
n30, n3h, usd3h = cur.fetchone()
print(f"  近 30min {n30} 腿 = {n30*2}/h；近 3h {n3h} 腿，Σ=${usd3h}")
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
print(f"  worker ok={j.get('ok')} equity={j.get('equity')} fills/h={j.get('fills_per_hour')}")
