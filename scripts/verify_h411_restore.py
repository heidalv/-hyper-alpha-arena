# -*- coding: utf-8 -*-
"""h411 硬上限恢复核实 + 判定任务补排。"""
import sys
import json
import pathlib
import datetime as dt
import subprocess
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
print("timeout_hard_taker_sec =", p.get("timeout_hard_taker_sec"))
t = m.get("h411_trial") or {}
print("h411_trial:", json.dumps({k: v for k, v in t.items()
                                 if k in ("started_at", "from", "to", "judge_at",
                                          "verdict", "guards_bypassed")},
                                ensure_ascii=False))
if t.get("started_at") and not t.get("verdict"):
    nxt = dt.datetime.now() + dt.timedelta(hours=12)
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h411_timeout_hard_taker_trial.py --judge")
    r2 = subprocess.run(["schtasks", "/Create", "/TN", "DSH_HFT_H411_JUDGE", "/TR", _tr,
                         "/SC", "ONCE", "/ST", nxt.strftime("%H:%M"),
                         "/SD", nxt.strftime("%Y/%m/%d"), "/F"],
                        capture_output=True, text=True, timeout=60)
    print(f"判定任务补排 @ {nxt:%Y-%m-%d %H:%M} rc={r2.returncode}")
