# -*- coding: utf-8 -*-
"""全部修复部署后核实 + h411 窗口污染记录。只读+一处元信息写入。"""
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
params = m.get("params") or {}

print("== 修复参数实测 ==")
for name, key, want in [
    ("T1 宽限归零", "stop_maker_grace_sec", 0.0),
    ("T5 急动冷却", "sudden_move_cooldown_sec", 90.0),
    ("T2 跳变速退", "jump_exit_bp", 12.0),
    ("T3 波动止损", "stop_loss_vol_min", 1.0),
    ("T4 薄盘加速", "max_one_side_seconds", 45.0),
]:
    v = float(params.get(key) or 0.0)
    print(f"  [{'OK' if abs(v-want) < 1e-9 else 'FAIL'}] {name:<10} {key}={v}")

print("\n== 试跑元信息 ==")
for k in ("h392_trial", "h429_trial", "h425_trial", "h426_trial", "h427_trial"):
    t = m.get(k) or {}
    print(f"  {k}: started={t.get('started_at','')[:16]} judge={t.get('judge_at','')[:16]} "
          f"bypassed={t.get('guards_bypassed')}")

# h411 窗口污染记录（诚实账本：修复项并行部署，h411 后 6h 窗口不可归因）
h411 = dict(m.get("h411_trial") or {})
if "contamination" not in h411:
    h411["contamination"] = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": "用户 2026-09-28 01:40 委任全部修复立即执行：h392(grace 0)+h429(cooldown 90)"
                "+h425(jump 12)+h426(vol_min 1.0)+h427(45s) 并行部署 ⇒ 本试跑 01:40 后窗口"
                "与上述变更混叠，判定结果不可单变量归因（audit 留痕）",
    }
    m["h411_trial"] = h411
    ops = list(m.get("ops_changes") or [])
    ops.append({"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
                "action": "h428_full_repair_deploy",
                "note": "用户委任：T1/T5/T2/T3/T4 五项并行部署（--force guards_bypassed）"})
    m["ops_changes"] = ops[-20:]
    cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id='mm_asterdex'",
                (json.dumps(m, ensure_ascii=False, default=str),))
    c.commit()
    print("\nh411_trial 已加 contamination 记录 + ops_changes 审计")
else:
    print("\nh411_trial 已有 contamination 记录（跳过）")
