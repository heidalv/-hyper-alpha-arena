# -*- coding: utf-8 -*-
"""今晚 40/h 是市场还是闸门？① 市场活跃度对照 ② 实时 skip 构成。只读。"""
import sys
import json
import pathlib
import time
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"), autocommit=True)
cur = c.cursor()
syms = ["BNB", "NEAR", "ARB", "XRP", "ENA"]
print("== 市场活跃度：成交桶数/小时（5 币合计）==")
cur.execute("""
    SELECT date_trunc('hour', to_timestamp(timestamp/1000)) AS h, COUNT(*)
    FROM market_trades_aggregated
    WHERE exchange='asterdex' AND symbol = ANY(%s)
      AND timestamp >= (EXTRACT(EPOCH FROM now())*1000 - 30*3600*1000)::bigint
    GROUP BY 1 ORDER BY 1
""", (syms,))
for h, n in cur.fetchall():
    print(f"  {h:%m-%d %H:%M}  {n:>6} 桶")

print("\n== 实时 skip 增量（2 分钟）==")
st = ROOT / "logs" / "mm_lane_status.json"
s1 = json.loads(st.read_text(encoding="utf-8"))
time.sleep(120)
s2 = json.loads(st.read_text(encoding="utf-8"))
k1 = {k: int(v) for k, v in (s1.get("skip_counts") or {}).items()}
k2 = {k: int(v) for k, v in (s2.get("skip_counts") or {}).items()}
diff = {k: k2.get(k, 0) - k1.get(k, 0) for k in set(k1) | set(k2)}
tot = sum(v for v in diff.values() if v > 0)
print(f"  2min 内 skip 总增量 {tot}；构成：")
for k, v in sorted(diff.items(), key=lambda x: -x[1])[:10]:
    if v:
        print(f"    {k:<26} {v:>+5}")
print(f"  quote 决策增量: {int(s2.get('quoted_decisions') or 0) - int(s1.get('quoted_decisions') or 0)}")
