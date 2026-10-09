# -*- coding: utf-8 -*-
"""[h824] 新链路首次实盘运行核查:往返日志 + 最近成交。"""
import importlib.util
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
rt = r"D:\001Alpha\Hyper-Alpha-Arena\data\flow_roundtrip_log.jsonl"
try:
    rows = [json.loads(x) for x in open(rt, encoding="utf-8").read().splitlines() if x.strip()]
    print(f"往返日志:{len(rows)} 条")
    for r in rows[-8:]:
        print(f"  {r.get('symbol')} {r.get('why')} y={r.get('y_bp')} "
              f"fee={r.get('fee_bp')} maker={r.get('maker')}")
except Exception:
    print("往返日志:尚无")

_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT ts, symbol, COALESCE(meta_json->>'exit_path','?'), net_bp, notional"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '30 minutes' ORDER BY ts DESC LIMIT 12")
    rows2 = cur.fetchall()
    print(f"近 30 分钟成交:{len(rows2)} 条")
    for r in rows2:
        print(f"  {r[0]:%H:%M:%S} {str(r[1])[:8]:<8} {str(r[2])[:20]:<20} "
              f"{float(r[3] or 0):+7.1f}bp")
