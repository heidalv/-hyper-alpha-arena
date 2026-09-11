# -*- coding: utf-8 -*-
"""Z123: `by_day` 切片验证（§58）——按 UTC 日看当前漏斗，避免长窗口混读。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.mlto.midlong_direction_audit import summarize_decision_funnel  # noqa: E402

s = summarize_decision_funnel(lookback_hours=24 * 6, by_day_days=6)
print(f"总计 n={s['n']} opened={s['opened']} skips={s['skips']} files={s['files']}")
print("\n按 UTC 日：")
for d in s.get("by_day", []):
    top = d["top_skip_reasons"][0] if d["top_skip_reasons"] else {}
    print(f"  {d['date']}  n={d['n']:<6} opened={d['opened']:<4} skips={d['skips']:<6} "
          f"top={top.get('reason', '-')} ({top.get('count', 0)})")
print("\n整体 top5:")
for r in s["top_skip_reasons"][:5]:
    print("   ", r)
