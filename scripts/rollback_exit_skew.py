# -*- coding: utf-8 -*-
"""按数学证据回滚 exit_skew_k（h453：库存消融与仓位规模无关，A-S 偏斜假设不成立）。"""
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
old = p.get("exit_skew_k")
p["exit_skew_k"] = 0.0
m["params"] = p
ops = list(m.get("ops_changes") or [])
now = dt.datetime.now(dt.timezone.utc).isoformat()
ops.append({"ts": now, "action": "h453_rollback_exit_skew",
            "field": "params.exit_skew_k", "from": old, "to": 0.0,
            "note": "数学证据回滚：h453 拟合 ln(持仓时长) 对 ln(峰值名义) b1=−0.058 (t=−1.06, "
                    "R²=0.001) ⇒ A-S「消融速率 ∝ |Inv|」在本车道不成立（时长由计时器/被动成交"
                    "主导）；h432 判定 delta=−0.369bp 亦为负 ⇒ 无证据支撑（目标①）"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print(f"exit_skew_k {old} → 0.0（已回滚；v2 层至此完全解除）")
