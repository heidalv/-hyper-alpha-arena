# -*- coding: utf-8 -*-
"""按**测试断言**恢复 `.env` 里被吞掉的声明（[新目标 R4] 事故修复第二步，2026-09-27）。

第一步（`repair_env_20260927.py`）只补回了"有单测/台账证据"和"与代码默认一致"的键，
把 50 个风险参数键留给人裁决。但跑全量 `-k env` 时暴露出 9 个红测试 —— 它们本身就是
**独立的、可执行的证据**：`assert ... 未在 .env 声明`、`assert env.get(KEY) == 值`。
本脚本把这些断言逐条抽出来核对 `.env`，只补齐"测试明确要求"的键，值取测试断言的字面量。

用法：.venv\\Scripts\\python.exe scripts\\restore_env_declared_keys_20260927.py [--apply]
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

#: (键, 期望值, 证据出处) —— 全部来自**已存在的测试断言**，不含任何我的推断
REQUIRED = [
    # backend/tests/unit/test_lane_separation_20260918.py::test_stage2_keys_declared_in_env_file
    ("MIDLONG_ATR_SL_MULT_MID", "1.5", "test_lane_separation_20260918::test_stage2_keys_declared_in_env_file"),
    ("MIDLONG_ATR_SL_MULT_LONG", "3.0", "同上"),
    ("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_MID", "14400", "同上"),
    ("MIDLONG_POSITION_MGMT_LLM_INTERVAL_SEC_LONG", "14400", "同上"),
    ("TIER_MID_MAX_HOLD_SEC", "172800", "同上（中线恢复 48h 时间尺度）"),
    # backend/tests/unit/test_sl_floor_cap_conflict_20260916.py
    ("MIDLONG_SL_MAX_PCT_MID", "0.03", "test_sl_floor_cap_conflict_20260916（mid/page 双源一致）"),
    ("MIDLONG_MAX_SL_PCT_MID", "0.03", "同上（旧名兼容键）"),
    ("MIDLONG_SL_MAX_PCT_LONG", "0.08", "同上"),
    ("MIDLONG_MAX_SL_PCT_LONG", "0.08", "同上（旧名兼容键）"),
    # backend/tests/unit/test_ai_coin_chain_20260916.py
    ("MIDLONG_MID_AI_CANDIDATES_ENABLED", "true", "test_ai_coin_chain_20260916::test_env_ai_coin_chain_enabled"),
    ("MIDLONG_MID_AI_SCAN_SLOTS", "2", "同上"),
    # backend/tests/unit/test_ai_strategy_provision_20260916.py
    ("MIDLONG_AI_AUTOCREATE_STRATEGY", "true", "test_ai_strategy_provision_20260916::test_deployed_env_defaults"),
    ("MIDLONG_AI_AUTOCREATE_MAX_PER_DAY", "8", "同上"),
    ("MIDLONG_AI_AUTOCREATE_LIVE", "false", "同上"),
    # backend/tests/unit/test_live_close_routing_20260918.py
    ("LIVE_SUB_POSITION_TRACKING", "false", "test_live_close_routing_20260918（要求显式声明，值 false）"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    vals = dotenv_values(str(ENV))
    missing, wrong, ok = [], [], []
    for key, want, why in REQUIRED:
        cur = vals.get(key)
        if cur is None:
            missing.append((key, want, why))
        elif str(cur).strip().lower() != want.lower():
            wrong.append((key, cur, want, why))
        else:
            ok.append(key)
    print(f"已正确声明 = {len(ok)}：{ok}")
    print(f"缺失 = {len(missing)}：")
    for k, v, why in missing:
        print(f"   {k}={v}   ← {why}")
    print(f"值不符 = {len(wrong)}：")
    for k, cur, want, why in wrong:
        print(f"   {k}: 现 {cur} → 期望 {want}   ← {why}")
    if not (missing or wrong):
        print("\n无需改动。")
        return 0

    block = ["", "# ── [2026-09-27 R4] 事故修复第二步：以下键的声明被吞进注释，按**测试断言**补回 ──",
             "# 证据逐条列在 scripts/restore_env_declared_keys_20260927.py 的 REQUIRED 表里。"]
    for k, v, why in missing + [(k, w, y) for k, _, w, y in wrong]:
        block.append(f"#   {k}={v}  ← {why}")
    for k, v, _ in missing + [(k, w, y) for k, _, w, y in wrong]:
        block.append(f"{k}={v}")

    if not apply:
        print("\n[dry-run] 将追加：")
        print("\n".join(block))
        return 0
    with ENV.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(block) + "\n")
    vals2 = dotenv_values(str(ENV))
    print(f"\n[apply] 已追加 {len(missing) + len(wrong)} 个键；dotenv 解析键数 {len(vals)} → {len(vals2)}")
    for key, want, _ in REQUIRED:
        got = vals2.get(key)
        flag = "OK " if str(got).strip().lower() == want.lower() else "BAD"
        print(f"   [{flag}] {key}={got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
