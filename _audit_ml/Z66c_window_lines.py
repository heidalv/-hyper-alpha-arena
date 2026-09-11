# -*- coding: utf-8 -*-
"""Z66c: 直接看 09-08 16:45~16:50(本地) 与 VIRTUAL 相关的日志原文。"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "logs" / "backend-console.log"
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
lo, hi = "2026-09-08 16:45", "2026-09-08 16:52"
n = 0
shown = 0
with p.open(encoding="utf-8", errors="ignore") as f:
    for line in f:
        m = rx_ts.match(line)
        if not m:
            continue
        minute = m.group(1)
        if minute < lo or minute > hi:
            continue
        if "VIRTUAL" not in line:
            continue
        n += 1
        if shown < 40:
            print(line.rstrip()[:230])
            shown += 1
print(f"\n--- 窗口内 VIRTUAL 行数: {n}（已显示 {shown}）---")

# 再看这段时间的全量行数，判断日志是否真的覆盖
tot = 0
with p.open(encoding="utf-8", errors="ignore") as f:
    for line in f:
        m = rx_ts.match(line)
        if m and lo <= m.group(1) <= hi:
            tot += 1
print("窗口内总行数:", tot)
