# -*- coding: utf-8 -*-
"""[h799c] 回滚 ofi_require 后的成交恢复检查。"""
import importlib.util
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
_spec = importlib.util.spec_from_file_location(
    "h425_trial", r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg
import time

time.sleep(90)
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    for mins in (3, 6, 10):
        cur.execute(
            "SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
            " WHERE lane_id='mm_asterdex' AND event='fill'"
            " AND ts > now() - (%s * interval '1 minute')", (mins,))
        n, u = cur.fetchone()
        print(f"近 {mins} 分钟: {n} 腿 净 {float(u or 0):+.3f}U")
