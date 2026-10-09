# -*- coding: utf-8 -*-
"""部署状态核对：修复链各步是否已部署 + 线上参数实测 + 近期盈亏。只读。"""
import sys
import json
import pathlib
import datetime as dt
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

now = dt.datetime.now()
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print(f"当前时间: {now:%Y-%m-%d %H:%M:%S}")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
params = m.get("params") or {}

print("\n== 修复项线上参数实测 ==")
checks = [
    ("T1 止损宽限归零", "stop_maker_grace_sec", 0.0),
    ("T5 急动冷却 90s", "sudden_move_cooldown_sec", 90.0),
    ("T2 跳变速退 12bp", "jump_exit_bp", 12.0),
    ("T3 波动条件止损 1.0", "stop_loss_vol_min", 1.0),
    ("T4 薄盘加速 45s", "max_one_side_seconds", 45.0),
]
for name, key, want in checks:
    cur_v = float(params.get(key) or 0.0)
    ok = abs(cur_v - want) < 1e-9
    print(f"  [{'✓' if ok else '✗'}] {name:<22} {key}={cur_v}（目标 {want}）")

print("\n== 修复链试跑状态 ==")
for k in ("h411_trial", "h392_trial", "h429_trial", "h425_trial",
          "h426_trial", "h427_trial"):
    t = m.get(k) or {}
    if not t:
        print(f"  {k}: 未部署")
        continue
    print(f"  {k}: verdict={t.get('verdict')} started={t.get('started_at','')[:16]} "
          f"judge={t.get('judge_at','')[:16]}")

print("\n== 近 2h 盈亏 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,2),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '2 hours'
""")
r = cur.fetchone()
print(f"  {r[0]} 腿，usd={r[1]}，均 net={r[2]} bp")

print("\n== 定时任务 ==")
import subprocess
r = subprocess.run(["schtasks", "/query", "/tn", "DSH_HFT_H428_QUEUE", "/fo", "list"],
                   capture_output=True, text=True, timeout=30)
for line in r.stdout.splitlines():
    if any(x in line for x in ("Next Run", "Last Run", "Status", "Last Result")):
        print("  H428:", line.strip())
r = subprocess.run(["schtasks", "/query", "/tn", "DSH_HFT_H411_JUDGE", "/fo", "list"],
                   capture_output=True, text=True, timeout=30)
for line in r.stdout.splitlines():
    if any(x in line for x in ("Next Run", "Last Run", "Status", "Last Result")):
        print("  H411_JUDGE:", line.strip())
