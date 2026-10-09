# -*- coding: utf-8 -*-
"""宇宙变化核查：symbols 5 个了——谁改的、什么时候、依据。只读。"""
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
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
print("symbols:", m.get("symbols"))
t = m.get("h356_trial") or {}
print("\nh356_trial:", json.dumps(t, ensure_ascii=False, default=str)[:600])
print("\nops_changes 末 6 条：")
for e in (m.get("ops_changes") or [])[-6:]:
    print("  ", json.dumps(e, ensure_ascii=False)[:200])
