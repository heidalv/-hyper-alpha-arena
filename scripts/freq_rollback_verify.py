# -*- coding: utf-8 -*-
"""频率修复记录：回滚 P2 三闸 + 20min 后腿速复测。写 ops_changes + 复测。"""
import sys
import json
import pathlib
import time
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import datetime as dt

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
ops = list(m.get("ops_changes") or [])
ops.append({"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "action": "h440_h439_h434_rollback",
            "note": "用户硬约束 ≥60 腿/h：P2 三闸（k_trend/p3_spike_gate/pullback_flow_block）"
                    "叠加后腿速降至 54-60/h ⇒ 按频率地板立即回滚（判定前手动回滚，审计留痕）；"
                    "保留 P0 vwap_flow_block 与 P1 trail/decay/hold 继续观察"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print("ops_changes 已记")

print("\n等 20 分钟复测腿速……", flush=True)
time.sleep(1200)
cur.execute("""
    SELECT COUNT(*) FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '20 minutes'
""")
n = cur.fetchone()[0]
print(f"回滚后近 20min: {n} 腿 = {n*3}/h")
