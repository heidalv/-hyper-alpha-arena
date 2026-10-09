# -*- coding: utf-8 -*-
"""#2 判决结果读取器（01:03 运行）：verdict + 宇宙变化 + 判决前后盈亏。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]

t = m.get("h356_trial") or {}
print("== #2 判决 ==")
print("verdict:", t.get("verdict", "(未判决)"))
print("judged_at:", t.get("judged_at"))
for k in ("why", "welch_p", "criteria", "result", "stats_era", "dropped", "kept", "verdict_note"):
    if k in t:
        v = t[k]
        print(f"{k}: {json.dumps(v, ensure_ascii=False)[:300]}")

print("\n== 当前宇宙 ==")
print("symbols:", m.get("symbols"))

print("\n== 判决时刻前 1h 盈亏 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,2),
           ROUND(AVG(net_bp)::numeric,2)
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '1 hour'
""")
r = cur.fetchone()
print(f"last 1h: {r[0]} legs, usd={r[1]}, mean_net={r[2]} bp")

print("\n== ops_changes 最近 3 条 ==")
try:
    cur.execute("""
        SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'
    """)
    oc = m.get("ops_changes") or []
    if isinstance(oc, list):
        for e in oc[-3:]:
            print(" ", json.dumps(e, ensure_ascii=False)[:250])
    else:
        print(" ", str(oc)[:400])
except Exception as e:
    print("  (ops_changes 读取失败)", e)
