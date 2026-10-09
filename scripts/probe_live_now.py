# -*- coding: utf-8 -*-
"""最后一查：近 40min 实时盈亏 + #2 判决是否已落。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,2),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '40 minutes'
""")
r = cur.fetchone()
print("last 40min:", r[0], "legs, usd=", r[1], ", mean_net=", r[2], "bp")
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("h356 verdict:", m.get("h356_trial", {}).get("verdict", "(not judged yet)"))
print("h356 judge_at:", m.get("h356_trial", {}).get("judge_at"))
