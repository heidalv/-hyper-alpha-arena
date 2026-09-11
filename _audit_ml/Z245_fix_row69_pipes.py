# -*- coding: utf-8 -*-
"""[§84] 修 #69 行里的转义竖线（`\\|` 会破坏 §62 表格解析 —— 这个坑本轮已踩第二次）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
hit = 0
for i, l in enumerate(lines):
    if l.startswith("| 69 |"):
        new = l.replace("`mid\\|trend_broken`", "`mid…trend_broken`") \
               .replace("`mid\\|midlong`", "`mid…midlong`")
        if new != l:
            lines[i] = new
            hit += 1
        print("修后:", lines[i][:120])
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("修正行数:", hit)
