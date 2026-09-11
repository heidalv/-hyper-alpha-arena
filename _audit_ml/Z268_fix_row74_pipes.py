# -*- coding: utf-8 -*-
"""[§91] 修 #74 行里的转义竖线（`\\|` 会破坏 §62 表格解析 —— 这是第三次踩同一个坑）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
for i, l in enumerate(lines):
    if l.startswith("| 74 |"):
        new = l
        for ch in ("short\\|max_hold_timeout", "short\\|sl", "mid\\|trend_broken"):
            new = new.replace("`" + ch + "`", "`" + ch.replace("\\|", "…") + "`")
        if new != l:
            lines[i] = new
            print("修后:", new[:130])
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("done")
