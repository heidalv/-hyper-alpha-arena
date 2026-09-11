# -*- coding: utf-8 -*-
"""[§88] #71 的状态改为"已审计 + 已处置（P29-C）"（决策已执行，不再是待决策）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
for i, l in enumerate(lines):
    if l.startswith("| 71 |") and "待决策 P29" in l:
        head = l.split("| 📋 **待决策 P29**")[0]
        lines[i] = (head + "| ✅ **已审计（P28-A）+ 已处置（P29-C）**：下单收口点加按层 SL 上限 3% "
                    "+ 风险预算 0.75%（见 #72 / §87–§88）；观察项 §88.4 |")
        print("已更新 #71 状态")
        break
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
