# -*- coding: utf-8 -*-
"""[h811b] 模型门生效后的进场是否真的停了(按分钟分桶)。"""
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
    cur.execute(
        "SELECT to_char(date_trunc('minute', ts), 'HH24:MI') m,"
        " COALESCE(meta_json->>'exit_path','maker') p, count(*)"
        " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
        " AND ts > now() - interval '50 minutes'"
        " GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC LIMIT 22")
    print("近 50 分钟逐分钟(时间 | 路径 | 笔数):")
    for r in cur.fetchall():
        print(f"  {r[0]}  {str(r[1])[:20]:<20} {r[2]}")
