# -*- coding: utf-8 -*-
"""按证据回滚 vwap_revert_bp（h354 判定 delta=+0.011bp 零效应，却拦掉 32% 的入场）。

背景：今晚实测市场活跃（22:00 988 桶 / 23:00 758 桶，全天 700-900），但腿速仅 35-42/h；
2 分钟 skip 构成：vwap_revert_up 12（32%）、vol_regime 8（21%）、trend_up 6、trend_down 4、
sudden_move 4、model_below_thr 3 —— 即"闸门拦截"而非"市场清淡"。
h354 的 P2 vwap 闸判定 INCONCLUSIVE / delta=+0.011bp（≈零效应）⇒ 按目标①（无证据支撑
一律回滚，且它正在压穿 ≥60 腿/h 硬约束）回滚。
⚠ 连带：`vwap_flow_block`（h433 试跑中）位于同一分支内，父闸关闭后自动失效（判定将显示无效应）。
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
old_vw, old_vfb = p.get("vwap_revert_bp"), p.get("vwap_flow_block")
p["vwap_revert_bp"] = 0.0
p["vwap_flow_block"] = 0.0
m["params"] = p
ops = list(m.get("ops_changes") or [])
now = dt.datetime.now(dt.timezone.utc).isoformat()
ops.append({"ts": now, "action": "h455_rollback_vwap_revert",
            "field": "params.vwap_revert_bp",
            "from": {"vwap_revert_bp": old_vw, "vwap_flow_block": old_vfb},
            "to": {"vwap_revert_bp": 0.0, "vwap_flow_block": 0.0},
            "note": "证据回滚：h354 判定 INCONCLUSIVE delta=+0.011bp（零效应）却拦 32% 入场；"
                    "今晚市场活跃（988/758 桶/h）而腿速仅 35-42/h ⇒ 闸门压穿 ≥60/h 硬约束；"
                    "连带 vwap_flow_block 一并关闭（同分支，父闸关即失效）"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print(f"vwap_revert_bp {old_vw} → 0；vwap_flow_block {old_vfb} → 0（已回滚）")
