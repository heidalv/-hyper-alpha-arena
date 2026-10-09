# -*- coding: utf-8 -*-
"""h429_trial 原始值 + 判定脚本手跑 dry-run。只读。"""
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
print("h429_trial 原始值:", json.dumps(m.get("h429_trial"), ensure_ascii=False))
print("params.sudden_move_cooldown_sec =", (m.get("params") or {}).get("sudden_move_cooldown_sec"))
