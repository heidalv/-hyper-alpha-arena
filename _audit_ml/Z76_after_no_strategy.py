# -*- coding: utf-8 -*-
"""Z76: 「无 active 策略」之后发生了什么？（是否紧接着创建策略 → 反应式创建）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEEDLE = "无 active 策略"
CREATED_HINTS = ("策略创建", "strategy_launcher", "创建策略", "launch", "promoted", "模板", "tpl_")

files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 500_000],
               key=lambda p: -p.stat().st_size)
shown = 0
for p in files:
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        continue
    idx = [i for i, l in enumerate(lines) if NEEDLE in l and "UNI" in l and "long" in l]
    if not idx:
        continue
    print(f"\n{'='*100}\n{p.name}: UNI/long 无策略行 {len(idx)} 条")
    for i in idx[:2]:
        print(f"--- 第 {i+1} 行起 后 12 行 ---")
        for j in range(i, min(len(lines), i + 12)):
            print("   ", lines[j][:200])
        # 该时刻附近是否出现策略创建类日志
        window = lines[max(0, i - 30): i + 30]
        hits = [l[:160] for l in window if any(h in l for h in CREATED_HINTS)]
        print(f"    ±30 行内创建类日志: {len(hits)}")
        for h in hits[:5]:
            print("       *", h)
        shown += 1
        if shown >= 4:
            break
    if shown >= 4:
        break
