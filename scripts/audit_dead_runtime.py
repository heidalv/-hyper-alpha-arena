# -*- coding: utf-8 -*-
"""断线审计 v3（运行时证据）：哪些闸 24h 内从未触发 / 参数处于中性值 / 状态只写不读。"""
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
print("== worker 状态快照顶层键 ==")
print("  ", sorted(j.keys()))

for k in ("skip_counts", "skips", "telemetry", "states", "params", "limits", "counters"):
    v = j.get(k)
    if isinstance(v, dict) and v and k not in ("states", "params", "limits"):
        print(f"\n== {k} ==")
        for kk, vv in sorted(v.items(), key=lambda x: -(x[1] if isinstance(x[1], (int, float)) else 0))[:20]:
            print(f"  {kk:<28} {vv}")

# 聚合所有 state 里的计数键
agg = {}
for sym, s in (j.get("states") or {}).items():
    if not isinstance(s, dict):
        continue
    for kk, vv in s.items():
        if isinstance(vv, (int, float)) and ("count" in kk or "hits" in kk or "blocked" in kk):
            agg[kk] = agg.get(kk, 0) + vv
print("\n== states 汇总计数（写入但可能无人读的信号）==")
for kk, vv in sorted(agg.items(), key=lambda x: -x[1]):
    print(f"  {kk:<28} {vv}")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
print("\n== 近 24h exit_reason 全分布（哪些出场真的在发生）==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_reason',''),'(none)') AS er, COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '24 hours'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 20
""")
for r in cur.fetchall():
    print(f"  {r[0]:<26} n={r[1]:>6} Σ=${r[2]:>+9}")

print("\n== 近 24h exit_action 分布 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_action',''),'(none)') AS ea, COUNT(*)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '24 hours'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 10
""")
for r in cur.fetchall():
    print(f"  {r[0]:<26} n={r[1]:>6}")

print("\n== 当前参数：处于中性/关闭值的开关（= 断的或未启用）==")
p = j.get("params") or {}
lim = j.get("limits") or {}
NEUTRAL_HINT = {
    "vpin_pause_threshold": 0, "frozen_width_bp": 0, "min_hold_seconds": 0,
    "trend_only_bp": 0, "ofi_flatten_threshold": 0, "jump_exit_bp": 0,
    "p1_hold_sec": 0, "p45_hold_sec": 0, "p3_spike_gate": 0,
    "p4_breakout_gate": 0, "p5_squeeze_gate": 0, "mp_block_bp": 0,
    "trail_lock_bp": 0, "post_stop_decay": 0, "pullback_flow_block": 0,
    "vwap_flow_block": 0, "k_trend": 0, "min_edge_frac": 0,
    "stop_loss_vol_min": 0, "sudden_move_cooldown_sec": 0, "min_width_reduce_bp": 0,
}
for k, zero in NEUTRAL_HINT.items():
    v = lim.get(k, p.get(k))
    if v is not None and float(v or 0) == float(zero):
        print(f"  {k:<26} = {v}（未启用）")
    elif v is not None:
        print(f"  {k:<26} = {v}（启用中）")
