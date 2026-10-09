# -*- coding: utf-8 -*-
"""[h859] 组合修复后的盈亏核查。"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (10, 15, 30, 60):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"  近 {mins:>2} 分钟: {n:>4} 腿 净 {float(u or 0):+8.3f}U")
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','(空)') p, count(*),"
        " SUM(net_bp*notional)/10000.0 FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '15 minutes' GROUP BY 1 ORDER BY 3")
    print("  近 15 分钟按路径:")
    for r in cur.fetchall():
        print(f"     {str(r[0])[:20]:<20} n={r[1]:>3} 净 {float(r[2] or 0):+8.3f}U")
