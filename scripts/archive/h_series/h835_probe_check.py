# -*- coding: utf-8 -*-
"""[h835] 探索通道上线后的成交核查。"""
import importlib.util
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
d = json.loads(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_lane_status.json",
                    encoding="utf-8").read())
print("skip 前 6:", dict(sorted((d.get("skip_counts") or {}).items(),
                                key=lambda x: -x[1])[:6]))
print("挂单:", d.get("side_counts"), "| ticks:", d.get("ticks"))
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute(
        "SELECT ts, symbol, COALESCE(meta_json->>'exit_path','?'), net_bp, notional"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '10 minutes' ORDER BY ts DESC LIMIT 8")
    rows = cur.fetchall()
    print(f"近 10 分钟成交 {len(rows)} 条:")
    for r in rows:
        print(f"  {r[0]:%H:%M:%S} {str(r[1]):<8} {str(r[2]):<20} "
              f"{float(r[3] or 0):+7.1f}bp 名义 {float(r[4] or 0):.2f}U")
