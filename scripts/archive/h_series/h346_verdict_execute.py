# -*- coding: utf-8 -*-
"""H346 试跑判决执行器（h345 协议的动作编码，杜绝判决时刻的临时发挥）。

# 用法（判决到点后，先人工确认 h296/h326 读数，再按三路之一执行）
   python scripts/h346_verdict_execute.py --verdict pass    # 达标：完结
   python scripts/h346_verdict_execute.py --verdict edge    # 边缘：延长观察
   python scripts/h346_verdict_execute.py --verdict fail    # 不达标：回退+激活备选

# 每个动作都：
   ① 恢复三个试跑期禁用的自动任务（EWA/learner/选币器）；
   ② 写 ops_changes（before/after/reason/evidence）；
   ③ 输出后续步骤（如需重启/建车道）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
FROZEN_TASKS = ["DSH_HFT_EWA_EVOLVER", "DSH_HFT_REVERSAL_LEARNER",
                "DSH_HFT_UNIVERSE_SELECT"]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def reenable_tasks():
    for t in FROZEN_TASKS:
        r = subprocess.run(["schtasks", "/Change", "/TN", t, "/ENABLE"],
                           capture_output=True, text=True, timeout=60)
        print(f"  恢复 {t}: {'OK' if r.returncode == 0 else 'FAIL ' + r.stderr[:80]}")


def log_ops(entry: dict):
    import psycopg
    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                " '{ops_changes}', COALESCE(meta_json->'ops_changes','[]'::jsonb) || %s::jsonb, true)"
                " WHERE lane_id=%s",
                (json.dumps(entry, ensure_ascii=False, default=str), LANE))
        conn.commit()


def set_param(key: str, value) -> None:
    import psycopg
    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lane_registry SET meta_json = jsonb_set(meta_json, %s, %s::jsonb, true)"
                " WHERE lane_id=%s",
                (f"{{params,{key}}}", json.dumps(value), LANE))
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdict", required=True, choices=["pass", "edge", "fail"])
    a = ap.parse_args()
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    print("=" * 72)
    if a.verdict == "pass":
        print("达标 → 目标完结。动作：恢复三个自动任务 + 登记完结。")
        reenable_tasks()
        log_ops({"by": "h346_verdict_pass", "op": "trial_verdict", "ts": now,
                 "before": "vpin_pause_threshold=0.60 试跑中", "after": "0.60 正式落地",
                 "reason": "h345 预注册判据达标（wnet_bp>0 且正小时≥50%）。VPIN 0.60 规则"
                           "正式落地；slow_rev 保持备选未启用。"})
        print("  完结。目标 goal-a992545e 可标记 complete。")
        return 0
    if a.verdict == "edge":
        print("边缘 → 延长观察 12h（参数不动）。动作：恢复自动任务 + 登记延长。")
        reenable_tasks()
        log_ops({"by": "h346_verdict_edge", "op": "trial_extend", "ts": now,
                 "before": "vpin=0.60", "after": "vpin=0.60（延长观察12h）",
                 "reason": "h345 判据边缘（wnet∈(-0.2,0]且正小时≥40%）。延长12h再判。"})
        print("  12h 后再跑本执行器。")
        return 0
    # fail
    print("不达标 → 回退 VPIN + 激活 slow_rev 备选。")
    reenable_tasks()
    set_param("vpin_pause_threshold", 0)
    log_ops({"by": "h346_verdict_fail", "op": "rollback_vpin", "ts": now,
             "before": "vpin_pause_threshold=0.60", "after": "0",
             "reason": "h345 判据不达标。回退 VPIN 门（热采用，无需重启）。"})
    print("  ✓ vpin_pause_threshold=0（60s 内热采用）")
    print("\n  备选激活步骤（slow_rev，人工执行）：")
    print("   1. 车道 meta：side_mode='slow_rev'、reversal_decay_bp=0、take_profit_bp=30、")
    print("      max_one_side_seconds=300、stop_loss_bp=40、trend_pause_bp=0、")
    print("      vpin_pause_threshold=0、slow_rev_min_bp=40")
    print("   2. 重启 worker：python scripts/h218_restart_worker.py")
    print("   3. 时代重锚 + ops_changes 登记（by=h346_slow_rev_activate）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
