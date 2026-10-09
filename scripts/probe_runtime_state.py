# -*- coding: utf-8 -*-
"""查 lane_runtime_state：worker 的运行态（含 SymbolState）是否落到这里。只读。"""
import sys
import json
import psycopg

ROOT = None
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name='lane_runtime_state' ORDER BY ordinal_position
""")
cols = [r[0] for r in cur.fetchall()]
print("lane_runtime_state 列:", cols)
cur.execute("SELECT * FROM lane_runtime_state WHERE lane_id='mm_asterdex' LIMIT 1")
row = cur.fetchone()
if row:
    d = dict(zip(cols, row))
    for k, v in d.items():
        s = str(v)
        print(f"  {k}: {s[:200]}")
    # 找 SymbolState 里是否含 ar300_hist
    for k, v in d.items():
        if isinstance(v, str) and "ar300_hist" in v:
            print(f"  ✓ 字段 {k} 含 ar300_hist")
        if isinstance(v, dict) and "ar300_hist" in json.dumps(v)[:100000]:
            print(f"  ✓ 字段 {k}(dict) 含 ar300_hist")
else:
    print("  （无该车道行）")
