# -*- coding: utf-8 -*-
"""Z117: §57 修复验证 —— 漏斗统计现在能看到轮转前的历史（对比修复前后口径）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.mlto.midlong_direction_audit import (  # noqa: E402
    audit_paths, summarize_decision_funnel,
)

ps = audit_paths()
print(f"audit_paths(): {len(ps)} 个文件")
for p in ps:
    print("   ", Path(p).name)

for hrs, label in ((24, "近 24h"), (24 * 40, "近 40 天（跨轮转）")):
    s = summarize_decision_funnel(lookback_hours=hrs)
    print(f"\n=== {label}: files={s['files']} n={s['n']} opened={s['opened']} "
          f"attempts={s['open_attempts']} skips={s['skips']} ===")
    print("   by_stage_skip:", s["by_stage_skip"])
    print("   top skip reasons:")
    for r in s["top_skip_reasons"][:8]:
        print(f"      {r['count']:>7}  {r['reason']}")
