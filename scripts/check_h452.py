# -*- coding: utf-8 -*-
"""h452 消失排查：注册表参数 + 试跑元信息 + ops 轨迹。只读。"""
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
cur.execute("SELECT meta_json, updated_at FROM lane_registry WHERE lane_id='mm_asterdex'")
m, upd = cur.fetchone()
p = m.get("params") or {}
print("registry updated_at:", upd)
print("trend_only_bp =", p.get("trend_only_bp"), "| ofi_confirm =", p.get("ofi_confirm_threshold"))
for k in ("h448_trial", "h452_trial"):
    t = m.get(k) or {}
    print(f"{k}:", json.dumps({kk: t.get(kk) for kk in
                               ("verdict", "started_at", "judge_at", "from", "to",
                                "rolled_back_at", "why")}, ensure_ascii=False))
print("\nops 末 5 条：")
for e in (m.get("ops_changes") or [])[-5:]:
    print("  ", json.dumps(e, ensure_ascii=False)[:190])
