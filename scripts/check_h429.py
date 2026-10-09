# -*- coding: utf-8 -*-
"""h429 判定结果核对。只读。"""
import sys
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
for k in ("h429_trial", "h392_trial", "h425_trial", "h426_trial", "h427_trial"):
    t = m.get(k) or {}
    print(f"{k}: verdict={t.get('verdict')} judged_at={t.get('judged_at')} "
          f"extend_until={t.get('extend_until')} why={t.get('why')}")
