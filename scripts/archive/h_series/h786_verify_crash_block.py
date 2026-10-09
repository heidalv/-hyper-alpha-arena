# -*- coding: utf-8 -*-
"""[h786] 当场验证:崩盘熔断(h784)是否真的在隔离 SI/BTW;没隔离就修。"""
import importlib.util
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

# 1) 亏损淘汰(4h)当前结果
from backend.services.market_maker.qspeed import pnl_decayed_coins
print("1) pnl_decayed_coins(4h 亏损淘汰):", pnl_decayed_coins("mm_asterdex"))

# 2) worker 日志里的崩盘事件
import subprocess
try:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Content 'D:\\001Alpha\\Hyper-Alpha-Arena\\logs\\mm_lane_worker.log' -Tail 300"
         " | Select-String -Pattern 'h784|crash|崩盘|decay_block|实时淘汰' | Select-Object -Last 8"],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("2) worker 日志里的熔断/淘汰事件:")
    for line in (out.stdout or "").splitlines():
        print("   ", line[:120])
except Exception as e:
    print("2) 日志读取失败:", e)

# 3) SI/BTW 近 30 分钟是否有新的加仓腿(该停了)
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT symbol, COALESCE(meta_json->>'exit_path','maker') p,"
        " COALESCE(meta_json->>'flatten','false') flat, count(*),"
        " SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND symbol IN ('SI','BTW') AND ts > now() - interval '30 minutes'"
        " GROUP BY 1,2,3 ORDER BY 1,4")
    print("3) SI/BTW 近 30 分钟(看是否还有新加仓):")
    for r in cur.fetchall():
        kind = "开仓/加仓" if str(r[2]).lower() != "true" else "平仓"
        print(f"    {r[0]:<5} {str(r[1])[:20]:<20} {kind} n={r[3]} 净 {float(r[4] or 0):+.3f}U")
    # 4) 当前持仓
    cur.execute(
        "SELECT symbol, SUM(qty) FROM lane_runtime_state WHERE lane_id=%s"
        " AND symbol IN ('SI','BTW') GROUP BY 1", ("mm_asterdex",))
    print("4) lane_runtime_state 里的 SI/BTW 持仓:", cur.fetchall())
