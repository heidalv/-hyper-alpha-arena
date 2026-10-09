# -*- coding: utf-8 -*-
"""T1 部署后核对：参数落地 + 试跑元信息 + 手动补排判定任务。"""
import sys
import json
import pathlib
import datetime as dt
import subprocess
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
params = m.get("params") or {}
print("stop_maker_grace_sec =", params.get("stop_maker_grace_sec"))
t = m.get("h392_trial") or {}
print("h392_trial:", json.dumps(t, ensure_ascii=False, default=str)[:300])
ops = m.get("ops_changes") or []
print("末条 ops:", json.dumps(ops[-1], ensure_ascii=False)[:200] if ops else "无")

# 判定任务是否已排（脚本崩在打印前，可能没排）
r = subprocess.run(["schtasks", "/query", "/tn", "DSH_HFT_H392_JUDGE", "/fo", "list"],
                   capture_output=True, text=True, timeout=30)
have = "ERROR" not in r.stdout
print("DSH_HFT_H392_JUDGE 已存在:", have)
if have:
    for line in r.stdout.splitlines():
        if "Next Run" in line or "Last Result" in line:
            print("  ", line.strip())

# 若未排且试跑已开始：手动补排 T+12h
if t.get("started_at") and not have:
    start = dt.datetime.fromisoformat(t["started_at"])
    nxt = start + dt.timedelta(hours=12)
    if nxt <= dt.datetime.now(dt.timezone.utc):
        nxt = dt.datetime.now() + dt.timedelta(hours=12)
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h392_grace_zero_trial.py --judge")
    r2 = subprocess.run(["schtasks", "/Create", "/TN", "DSH_HFT_H392_JUDGE", "/TR", _tr,
                         "/SC", "ONCE", "/ST", nxt.strftime("%H:%M"),
                         "/SD", nxt.strftime("%Y/%m/%d"), "/F"],
                        capture_output=True, text=True, timeout=60)
    print(f"已补排判定任务 @ {nxt:%Y-%m-%d %H:%M} rc={r2.returncode}")
