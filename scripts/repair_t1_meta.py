# -*- coding: utf-8 -*-
"""T1 试跑元信息修复：h392_trial 被 worker 回写覆盖 → 重新写入并验证持久性。"""
import sys
import json
import pathlib
import datetime as dt
import time
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
params = dict(m.get("params") or {})

now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
m["h392_trial"] = {
    "started_at": now_iso, "from_grace": 30.0, "to_grace": 0.0,
    "baseline_symbols": [str(s) for s in (m.get("symbols") or []) if str(s)],
    "rollback_to": 30.0,
    "judge_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat(),
    "criteria": "A legs/h>=0.8x baseline; B per-leg net_bp Welch alpha=0.10; "
                "C stop leg mean should narrow (-65.4 -> ~-44)",
    "guards_bypassed": True,
    "note": "user_mandate_force_deploy_20260928_0138（h392 --force；GBK 打印崩溃前参数已落地，"
            "worker 回写吞掉了 trial 键——本脚本修复）",
}
ops = list(m.get("ops_changes") or [])
ops.append({"ts": now_iso, "action": "h392_grace_zero_deploy",
            "field": "params.stop_maker_grace_sec", "from": 30.0, "to": 0.0,
            "note": "T1 宽限归零（用户 01:38 强制部署委任；--force guards_bypassed）"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print("h392_trial 已重写，judge_at =", m["h392_trial"]["judge_at"])

# 验证持久性：等 70s（覆盖 worker 回写周期）再读
print("等 70s 验证持久性……", flush=True)
time.sleep(70)
with psycopg.connect(read_env_dsn(), autocommit=True) as c2:
    with c2.cursor() as cur2:
        cur2.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
        m2 = cur2.fetchone()[0]
t2 = m2.get("h392_trial") or {}
print("70s 后 h392_trial:", "存在 ✓" if t2.get("started_at") else "又被吞了 ✗")
print("70s 后 grace =", (m2.get("params") or {}).get("stop_maker_grace_sec"))

# 补排判定任务
nxt = dt.datetime.now() + dt.timedelta(hours=12)
_tr = ("wscript.exe //B //Nologo "
       r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
       r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
       r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h392_grace_zero_trial.py --judge")
r2 = subprocess.run(["schtasks", "/Create", "/TN", "DSH_HFT_H392_JUDGE", "/TR", _tr,
                     "/SC", "ONCE", "/ST", nxt.strftime("%H:%M"),
                     "/SD", nxt.strftime("%Y/%m/%d"), "/F"],
                    capture_output=True, text=True, timeout=60)
print(f"判定任务已排 @ {nxt:%Y-%m-%d %H:%M} rc={r2.returncode}")
