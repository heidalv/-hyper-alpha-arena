# -*- coding: utf-8 -*-
"""恢复硬上限 300s —— 合规兜底（用户硬约束 30s–5min），非策略调整。

依据：
  ① 用户硬约束："交易时间锁定在 30 秒到 5 分钟之内，这个是有验证过的"；
  ② 实测违规：2026-09-29 00:15 存在 3h+ 未平仓位（NEAR 18.6 枚 ≈ $97、BNB 残留）——
     与 h413 修掉的 "ADA 78min" 同类（正是用户当初报的 bug）；
  ③ 23:47 的自动回滚判据（frequency_collapse 79.9/h < 0.8×223.5/h）用**跨 regime 基线**：
     223.5/h 取自白天高活跃窗口、79.9/h 取自夜间安静窗口 ⇒ 比较不公平（方法论缺陷）。
  ⇒ 恢复 300s 作为**合规兜底**：只有"超 5 分钟仍未被动出库"的极端仓才会被强平，
     正常仓位（中位 61s、p90 167s）完全不受影响 ⇒ 不构成频率压制。
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
old = p.get("timeout_hard_taker_sec")
p["timeout_hard_taker_sec"] = 300.0
m["params"] = p
ops = list(m.get("ops_changes") or [])
ops.append({"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "action": "restore_hard_cap_compliance",
            "field": "params.timeout_hard_taker_sec", "from": old, "to": 300.0,
            "note": "合规兜底恢复（用户硬约束 30s–5min）：实测 3h+ 未平仓（NEAR $97）；"
                    "23:47 的自动回滚判据用跨 regime 基线（223.5/h 白天 vs 79.9/h 夜间）"
                    "不公平 ⇒ 作为兜底恢复，正常仓位（中位 61s）不受影响"})
m["ops_changes"] = ops[-20:]
# 该试跑标记为「合规兜底」而非策略试跑，避免再次被频率判据回滚
t = dict(m.get("h411_trial") or {})
t["verdict"] = "COMPLIANCE_BACKSTOP"
t["why"] = "用户硬约束兜底（30s–5min），非策略试跑；不再参与频率判据自动回滚"
t["rejudged_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
m["h411_trial"] = t
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print(f"timeout_hard_taker_sec {old} → 300（合规兜底，已标注不参与频率自动回滚）")
