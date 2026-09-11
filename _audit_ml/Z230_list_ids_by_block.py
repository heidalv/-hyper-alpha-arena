# -*- coding: utf-8 -*-
"""列出 §62 各块的 ID 序列（找出缺行），并打印疑似"撤销"行的原文。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
           if l.startswith("## 6") and not l.startswith("## 62."))
block = "?"
for i in range(start, end):
    l = lines[i]
    if l.startswith("### 62."):
        block = l.strip()[:60]
        print(f"\n### {block}")
        continue
    if l.startswith("| ") and l.count("|") >= 6:
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) >= 5 and not set(cells[0]) <= set("-: ") and cells[0] != "ID":
            print(f"  id={cells[0]:>6} | sev={cells[1][:8]:8} | status={cells[-1][:26]}")
print("\n=== 含 '146' 或 '双身份' 的行 ===")
for i, l in enumerate(lines, 1):
    if l.startswith("| ") and ("146" in l or "双身份" in l or "撤销" in l):
        print(f"{i}: {l[:200]}")
