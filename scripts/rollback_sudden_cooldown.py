# -*- coding: utf-8 -*-
"""按证据回滚 sudden_move_cooldown_sec（h429 判定 INCONCLUSIVE/delta=−0.470，且实测拦频）。

实测（2026-09-28 23:30-00:20）：2 分钟 skip 增量 sudden_move +8（≈4/min），
配合 90s 冷却 ⇒ 大幅时段被暂停、fills 2 分钟为 0 ⇒ 直接压穿 ≥60 腿/h 硬约束。
h429 判定：INCONCLUSIVE / delta=−0.470bp（无正面证据）⇒ 按目标①回滚。
"""
import sys
import json
import pathlib
import datetime as dt
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
old = p.get("sudden_move_cooldown_sec")
p["sudden_move_cooldown_sec"] = 0.0
m["params"] = p
ops = list(m.get("ops_changes") or [])
ops.append({"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "action": "h456_rollback_sudden_cooldown",
            "field": "params.sudden_move_cooldown_sec", "from": old, "to": 0.0,
            "note": "证据回滚：h429 判定 INCONCLUSIVE delta=−0.470bp（无正面证据）；"
                    "实测 2min sudden_move 拦 8 次 × 90s 冷却 ⇒ 大幅停摆、fills 归零，"
                    "压穿 ≥60 腿/h 硬约束 ⇒ 恢复为「只暂停命中那一 tick」（旧行为）"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print(f"sudden_move_cooldown_sec {old} → 0（已回滚）")
