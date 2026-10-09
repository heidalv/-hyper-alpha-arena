# -*- coding: utf-8 -*-
"""[h800c] 验证 active_flow_mode 是否真的在跑。"""
import importlib.util
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

# 1) 注册表参数
from backend.services import lane_registry as reg
p = ((reg.get_lane("mm_asterdex") or {}).get("meta") or {}).get("params") or {}
print("params.active_flow_mode =", p.get("active_flow_mode"))

# 2) 车道状态
d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json", encoding="utf-8").read())
print("side_counts:", d.get("side_counts"))
sk = d.get("skip_counts") or {}
act_keys = [k for k in sk if "flow" in k or "holding" in k or "no_" in k]
print("active 相关 skip:", {k: sk[k] for k in act_keys} if act_keys else "(无 —— active 分支没跑!)")
print("全部 skip 前 8:", dict(sorted(sk.items(), key=lambda x: -x[1])[:8]))

# 3) 最近的成交(active 模式的出场路径应为 flow_entry/sl/tp/flow_flip/max_hold)
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT ts, symbol, COALESCE(meta_json->>'exit_path','maker'), net_bp"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '25 minutes' ORDER BY ts DESC LIMIT 8")
    print("近 25 分钟成交:")
    for r in cur.fetchall():
        print(f"  {r[0]:%H:%M:%S} {str(r[1])[:8]:<8} {str(r[2])[:18]:<18} {float(r[3] or 0):+7.1f}bp")
