# -*- coding: utf-8 -*-
"""验证自适应闸的运行时状态：ar300_hist 是否在累积（找 worker 的持久化状态）。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("== 候选状态文件 ==")
for pat in ("logs/*state*.json", "logs/*lane*.json", "research_l1/out/*state*.json"):
    for p in sorted(ROOT.glob(pat)):
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size}B  {p.stat().st_mtime:.0f}")

print("\n== lane_registry 里是否存 states ==")
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("  meta 顶层键:", sorted(m.keys()))
st = m.get("states")
if isinstance(st, dict):
    bnb = st.get("BNB") or {}
    print("  meta.states.BNB 键:", sorted(bnb.keys()))
    print("  ar300_hist 长度:", len(bnb.get("ar300_hist") or []))

print("\n== 数据库里的运行态表（若有）==")
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' AND table_name LIKE '%lane%'
    ORDER BY 1
""")
print("  ", [r[0] for r in cur.fetchall()])
