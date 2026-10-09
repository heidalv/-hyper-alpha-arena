# -*- coding: utf-8 -*-
"""[h837] 体检 + 性能调研:车道是否正常,热路径耗时在哪。"""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")

# ── 1. 进程/心跳 ────────────────────────────────────────────────
ps = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
     "Where-Object { $_.CommandLine -match 'mm_lane_worker' } | "
     "ForEach-Object { \"{0}|{1}\" -f $_.ProcessId, $_.CreationDate }"],
    capture_output=True, text=True)
print("1) worker 进程:", (ps.stdout or "").strip() or "(无!)")

st = ROOT / "logs" / "mm_lane_status.json"
d = json.loads(st.read_text(encoding="utf-8"))
age = time.time() - st.stat().st_mtime
print(f"2) 状态文件: {age:.0f}s 前 | ticks={d.get('ticks')} | "
      f"fills/h={d.get('fills_per_hour')} | 宇宙 {len(d.get('states') or {})} 币")
print(f"   skip 前 5: {dict(sorted((d.get('skip_counts') or {}).items(), key=lambda x: -x[1])[:5])}")
print(f"   挂单: {d.get('side_counts')}")

# ── 2. 成交与盈亏 ───────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (15, 60):
        cur.execute("SELECT count(*), SUM(net_bp*notional)/10000.0, AVG(net_bp) "
                    "FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill' "
                    "AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u, a = cur.fetchone()
        print(f"3) 近 {mins} 分钟: {n} 腿 净 {float(u or 0):+.3f}U 每腿 {float(a or 0):+.2f}bp")
    cur.execute("SELECT COALESCE(meta_json->>'exit_path','?'), count(*), "
                "SUM(net_bp*notional)/10000.0 FROM lane_ledger "
                "WHERE lane_id='mm_asterdex' AND event='fill' "
                "AND ts > now() - interval '30 minutes' GROUP BY 1 ORDER BY 3")
    print("   近 30 分钟按路径:")
    for r in cur.fetchall():
        print(f"     {str(r[0])[:20]:<20} n={r[1]:>3} 净 {float(r[2] or 0):+.3f}U")

# ── 3. 热路径耗时(逐段计时)────────────────────────────────────
print("4) 热路径耗时(单次调用,ms):")
t0 = time.perf_counter()
for _ in range(20):
    json.loads((ROOT / "data" / "flow_situation_last.json").read_text(encoding="utf-8"))
t_sit = (time.perf_counter() - t0) / 20 * 1000
sz = (ROOT / "data" / "flow_situation_last.json").stat().st_size / 1024
print(f"   读+解析 情况表({sz:.0f}KB): {t_sit:.2f} ms  ← 每 tick 每币一次?")
t0 = time.perf_counter()
for _ in range(20):
    json.loads((ROOT / "data" / "flow_gate_last.json").read_text(encoding="utf-8"))
t_gate = (time.perf_counter() - t0) / 20 * 1000
print(f"   读+解析 生产门: {t_gate:.2f} ms")
from backend.services.market_maker.flow_rules import situation_decision  # noqa: E402
sit = json.loads((ROOT / "data" / "flow_situation_last.json").read_text(encoding="utf-8"))
sym = list((sit.get("coins") or {}).keys())[0] if (sit.get("coins") or {}) else "BTC"
t0 = time.perf_counter()
for _ in range(200):
    situation_decision(sit, sym, time.time(), 100.0, 100.0, 6.0)
t_dec = (time.perf_counter() - t0) / 200 * 1000
print(f"   situation_decision({sym}): {t_dec:.3f} ms")

# ── 4. tick 间隔与实际节拍 ──────────────────────────────────────
log = ROOT / "logs" / "mm_lane_worker.log"
if log.exists():
    lines = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
             if "[mm-worker]" in l][-6:]
    print("5) worker 日志尾部:")
    for l in lines:
        print("   " + l[:150])
