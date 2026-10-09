# -*- coding: utf-8 -*-
"""清理 h443 首版的 fill_notional 残留 + 核实实际单腿名义。"""
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
p = dict(m.get("params") or {})
p["fill_notional"] = 300.0     # 恢复原值（实际单腿由 equity×compound_ratio 决定）
m["params"] = p
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print("fill_notional 已恢复 300；compound_ratio =", p.get("compound_ratio"))
