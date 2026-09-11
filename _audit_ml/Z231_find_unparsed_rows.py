# -*- coding: utf-8 -*-
"""在 §62 范围内找出「未被解析为清单行」的表格行（定位缺失的 ⚪ 记录 行）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
           if l.startswith("## 6") and not l.startswith("## 62."))
print(f"§62: {start+1}~{end}")
bad = []
for i in range(start, end):
    l = lines[i]
    if not l.startswith("|"):
        continue
    n = l.count("|")
    if n < 6:
        bad.append((i + 1, n, l))
print(f"列数 <6 的表行 {len(bad)} 行：")
for ln, n, l in bad:
    print(f"  {ln} (pipes={n}): {l[:200]}")
print("\n含 ⚪ 的行（全文）：")
for i, l in enumerate(lines, 1):
    if "⚪" in l:
        print(f"  {i}: {l[:160]}")
