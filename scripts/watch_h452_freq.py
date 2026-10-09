# -*- coding: utf-8 -*-
"""h452 频率风控监视：25 分钟后测腿速与 trend_only 拦截占比，破 60/h 则报警。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE ts >= now() - interval '25 minutes'),
           COUNT(*) FILTER (WHERE ts >= now() - interval '10 minutes')
    FROM lane_ledger WHERE event='fill'
""")
n25, n10 = cur.fetchone()
rate = n25 * (60 / 25)
print(f"h452 后：近 25min {n25} 腿 = {rate:.0f}/h；近 10min {n10} 腿 = {n10*6}/h")
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
sk = j.get("skip_counts") or {}
print("相关 skip:", {k: v for k, v in sk.items() if "trend" in k or "flat" in k})
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '25 minutes'
    GROUP BY 1 ORDER BY 2 DESC
""")
print("近 25min 出场路径:")
for r in cur.fetchall():
    print(f"  {str(r[0]):<22} n={r[1]:>4} Σ=${r[2]:>+7}")
print("equity:", j.get("equity"), "| ok:", j.get("ok"))
print(f"\n判定：{'⚠ 破 60/h 地板' if rate < 60 else '✓ 频率达标'}")
