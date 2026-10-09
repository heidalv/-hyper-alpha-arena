# -*- coding: utf-8 -*-
"""按数学证据回滚 vol_spread_k（h445 拟合：价差-σ 耦合系数远小于线上设定，无支持）。

证据（168h，43,659 样本，5 币）：
    spread = 3.83 + 0.133·σ   t=11.3  R²=0.003   ← 线性最优
    spread = 3.86 + 0.010·σ²  t=4.3   R²=0.0004
线上 vol_spread_k=0.5（σ 每 +1 价差倍数 ×1.5）+ h432 判定 delta=−0.369bp（为负）
⇒ 无证据支撑，回滚为 0（exit_skew_k 暂留——库存消融的数学检验是下一项工作）。
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
old = p.get("vol_spread_k")
p["vol_spread_k"] = 0.0
m["params"] = p
ops = list(m.get("ops_changes") or [])
now = dt.datetime.now(dt.timezone.utc).isoformat()
ops.append({"ts": now, "action": "h445_rollback_vol_spread",
            "field": "params.vol_spread_k", "from": old, "to": 0.0,
            "note": "数学证据回滚：h445 拟合 spread=3.83+0.133σ (t=11.3, R²=0.003) 远小于 "
                    "线上 vol_spread_k=0.5 的隐含效应；h432 判定 delta=−0.369bp 亦为负 ⇒ "
                    "无证据支撑（目标①：无证据一律回滚）"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print(f"vol_spread_k {old} → 0.0（已回滚，ops 审计留痕）")
print("exit_skew_k 保留 =", p.get("exit_skew_k"), "（待库存消融数学检验）")
