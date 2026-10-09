# -*- coding: utf-8 -*-
"""恢复被错误判据误杀的功能 + 合并丢失的判定结果。

背景（2026-09-29 00:56 批次）：
  · 5 个判定任务并行运行 → **读改写竞争**（h433/h435/h437/h438 的判定写进注册表后被覆盖）
  · 判据用 0.8×基线（223/h 白天）比夜间 65.2/h ⇒ 全部误判 frequency_collapse
  · 绝对腿速 65.2/h 实际**高于用户 60/h 硬约束** ⇒ 误杀
处置：
  ① 从判定 JSON 合并回注册表，并标注 spurious_rollback（保留审计）
  ② 重新部署 4 项被误杀的功能（trail_lock / post_stop_decay / p1_hold / p45_hold）
     作为新试跑（判据已修正为绝对 60/h）
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

# ① 合并判定 JSON + 标注误杀
now = dt.datetime.now(dt.timezone.utc).isoformat()
merged = []
for key in ("h433", "h435", "h437", "h438"):
    p = ROOT / "research_l1" / "out" / f"{key}_verdict.json"
    if not p.exists():
        continue
    vd = json.loads(p.read_text(encoding="utf-8"))
    tk = f"{key}_trial"
    t = dict(m.get(tk) or {})
    t["verdict"] = vd.get("verdict")
    t["judged_at"] = vd.get("judged_at")
    t["why"] = vd.get("why")
    t["spurious_rollback"] = True
    t["spurious_reason"] = ("判据用跨 regime 基线（0.8×223/h 白天）比夜间 65.2/h；"
                            "绝对腿速高于用户 60/h 硬约束 ⇒ 误杀（h458 已修正判据）")
    m[tk] = t
    merged.append(key)

ops = list(m.get("ops_changes") or [])
ops.append({"ts": now, "action": "h458_merge_spurious_verdicts",
            "note": f"合并并行判定丢失的结果 {merged}；标注 spurious_rollback（跨 regime 基线误杀）；"
                    "判据已改为绝对 ≥60 腿/h（用户硬约束）"})
m["ops_changes"] = ops[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m, ensure_ascii=False, default=str),))
c.commit()
print("已合并判定:", merged)

# ② 恢复被误杀的参数
RESTORE = {"trail_lock_bp": 20.0, "post_stop_decay": 0.5,
           "p1_hold_sec": 60.0, "p45_hold_sec": 300.0}
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m2 = cur.fetchone()[0]
p = dict(m2.get("params") or {})
for k, v in RESTORE.items():
    p[k] = v
m2["params"] = p
ops2 = list(m2.get("ops_changes") or [])
ops2.append({"ts": now, "action": "h458_restore_after_spurious_rollback",
             "fields": RESTORE,
             "note": "恢复 4 项被误杀的功能（trail_lock #17 / post_stop_decay #16③ / "
                     "p1_hold+p45_hold h404）；各自重挂 T+12h 判定（判据已修正）"})
m2["ops_changes"] = ops2[-20:]
cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
            (json.dumps(m2, ensure_ascii=False, default=str),))
c.commit()
print("已恢复参数:", RESTORE)
