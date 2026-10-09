# -*- coding: utf-8 -*-
"""[h802] PENGU 开仓 0.000000 现场重建。"""
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
    cur.execute(
        "SELECT ts, meta_json->>'side', (meta_json->>'qty')::float,"
        " (meta_json->>'fill_px')::float, COALESCE(meta_json->>'exit_path','maker'),"
        " COALESCE((meta_json->>'avg_px')::float, -1)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND symbol='PENGU' AND ts > now() - interval '40 minutes' ORDER BY ts")
    print("PENGU 近 40 分钟成交:")
    for r in cur.fetchall():
        print(f"  {r[0]:%H:%M:%S} {str(r[1]):<4} qty={float(r[2] or 0):>10.2f} "
              f"px={float(r[3] or 0):.6f} {str(r[4])[:18]:<18} meta.avg={float(r[5] or -1):.6f}")
    cur.execute(
        "SELECT state_json->>'qty', state_json->>'avg_px' FROM lane_runtime_state"
        " WHERE lane_id='mm_asterdex' AND symbol='PENGU'")
    row = cur.fetchone()
    print(f"runtime_state PENGU: qty={row[0]} avg_px={row[1]}" if row else "runtime_state 无 PENGU")
d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json", encoding="utf-8").read())
st = (d.get("states") or {}).get("PENGU") or {}
print(f"status PENGU: qty={st.get('qty')} avg_px={st.get('avg_px')} quote=({st.get('quote_bid')},{st.get('quote_ask')})")
