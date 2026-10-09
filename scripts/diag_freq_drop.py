# -*- coding: utf-8 -*-
"""腿速下降排查：worker 状态 + skip 构成 + 近 30min 是否有报价。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
print("worker ok:", j.get("ok"), "| ticks:", j.get("ticks"), "| fills:", j.get("fills"),
      "| fills_per_hour:", j.get("fills_per_hour"), "| last_tick:", j.get("last_tick_ts"))
sk = j.get("skip_counts") or {}
tot_skip = sum(v for v in sk.values() if isinstance(v, (int, float)))
print(f"\nskip_counts 合计={tot_skip}，前 12：")
for k, v in sorted(sk.items(), key=lambda x: -x[1])[:12]:
    print(f"  {k:<26} {v}")

print("\n== 近 10min 账本 ==")
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT COUNT(*), MAX(ts) FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '10 minutes'
""")
r = cur.fetchone()
print(f"  腿数={r[0]} 最后成交={r[1]}")

print("\n== 逐币近 15min 腿数 ==")
cur.execute("""
    SELECT symbol, COUNT(*) FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '15 minutes'
    GROUP BY 1 ORDER BY 2 DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<6} {r[1]}")
