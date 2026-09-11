# -*- coding: utf-8 -*-
"""列出 §62 清单区里所有含 U+FFFD 的行（定位机器可读部分的损坏点）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
end = next(i for i, l in enumerate(lines[start + 1:], start + 1) if l.startswith("## 6") and not l.startswith("## 62."))
print(f"§62 行范围: {start+1} ~ {end}")
bad_rows, bad_other = [], []
for i in range(start, end):
    if "\ufffd" in lines[i]:
        if lines[i].startswith("| ") and lines[i].count("|") >= 6:
            bad_rows.append((i + 1, lines[i]))
        else:
            bad_other.append((i + 1, lines[i]))
print(f"\n受损**清单行** {len(bad_rows)} 行：")
for ln, l in bad_rows:
    print(f"  {ln}: {l[:150]}")
print(f"\n受损其它行 {len(bad_other)} 行（前 12）：")
for ln, l in bad_other[:12]:
    print(f"  {ln}: {l[:130]}")
ids = []
for ln, l in bad_rows:
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    ids.append(cells[0])
print("\n受损行 ID 单元:", ids)
