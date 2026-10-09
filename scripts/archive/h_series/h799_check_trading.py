# -*- coding: utf-8 -*-
"""[h799] 检查当前是否还有交易(用户:都没有交易了)。"""
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

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (15, 30, 60):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"近 {mins} 分钟成交: {n} 腿, 净 {float(u or 0):+.3f}U")
    cur.execute(
        "SELECT ts, symbol, meta_json->>'exit_path', net_bp, notional"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '40 minutes' ORDER BY ts DESC LIMIT 8")
    print("最近成交:")
    for r in cur.fetchall():
        print(f"  {r[0]:%H:%M:%S} {str(r[1])[:8]:<8} {str(r[2] or 'maker')[:16]:<16} "
              f"{float(r[3] or 0):+7.1f}bp ${float(r[4] or 0):>5.0f}")
d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json", encoding="utf-8").read())
print(f"状态: fills_per_hour={d.get('fills_per_hour')} ticks={d.get('ticks')}")
print(f"挂单: {d.get('side_counts')}")
print(f"skip 计数: {dict(list((d.get('skip_counts') or {}).items())[:10])}")
