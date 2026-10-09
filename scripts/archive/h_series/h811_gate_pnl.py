# -*- coding: utf-8 -*-
"""[h811] 模型门生效后的车道 P&L 复核。"""
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
    for mins in (30, 60, 240):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"  近 {mins} 分钟: {n} 腿 净 {float(u or 0):+.3f}U")
    cur.execute(
        "SELECT COALESCE(meta_json->>'exit_path','maker') p, count(*),"
        " SUM(net_bp*notional)/10000.0 FROM lane_ledger"
        " WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '1 hour' GROUP BY 1 ORDER BY 3")
    print("近 1h 按出场路径:")
    for r in cur.fetchall():
        print(f"  {str(r[0])[:22]:<22} n={r[1]:>3} 净 {float(r[2] or 0):+.3f}U")
    cur.execute(
        "SELECT position_id, COUNT(*) FROM lane_ledger WHERE lane_id='mm_asterdex'"
        " AND event='fill' AND ts > now() - interval '30 minutes'"
        " GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
    print("近 30 分钟未闭合仓位(样本):", cur.fetchall())
