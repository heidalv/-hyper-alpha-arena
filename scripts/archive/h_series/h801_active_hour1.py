# -*- coding: utf-8 -*-
"""[桥 14:58] 主动流模式首小时验证。"""
import importlib.util
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','maker') p, count(*),"
        " AVG(net_bp), SUM(net_bp*notional)/10000.0"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '1 hour' GROUP BY 1 ORDER BY 4")
    print("== 近 1h 出场路径 == ")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:22]:<22} n={r[1]:>3} 每腿 {float(r[2] or 0):+7.1f}bp 净 {float(r[3] or 0):+7.3f}U")
    cur.execute(
        "SELECT count(*), SUM(net_bp*notional)/10000.0, AVG(net_bp)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '40 minutes'")
    n, u, a = cur.fetchone()
    print(f"近 40 分钟合计: {n} 腿 {float(u or 0):+.3f}U 每腿 {float(a or 0):+.2f}bp")

d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json", encoding="utf-8").read())
print(f"车道: fills_ph={d.get('fills_per_hour')} day={d.get('day_pnl_usd')} eq={d.get('equity')}")
print(f"side={d.get('side_counts')}")
sk = d.get("skip_counts") or {}
print(f"skip 前 6: {dict(sorted(sk.items(), key=lambda x: -x[1])[:6])}")
