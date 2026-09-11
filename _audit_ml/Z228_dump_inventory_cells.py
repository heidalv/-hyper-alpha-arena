# -*- coding: utf-8 -*-
"""打印 §62 所有清单行的「ID / 严重度单元 / 状态单元」（看清哪些字符损坏）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = P.read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
end = next(i for i, l in enumerate(lines[start + 1:], start + 1) if l.startswith("## 6") and not l.startswith("## 62."))
for i in range(start, end):
    l = lines[i]
    if not (l.startswith("| ") and l.count("|") >= 6):
        continue
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    if len(cells) < 5:
        print(f"{i+1}: [列数异常 {len(cells)}] {l[:100]}")
        continue
    rid, sev, st = cells[0], cells[1], cells[-1]
    if rid in ("ID", "---") or set(rid) <= set("-: "):
        continue
    flag = "  <== 缺 U+FFFD" if "\ufffd" in rid else ""
    print(f"{i+1}: id={rid!r:8} sev={sev!r:12} status={st[:60]!r}{flag}")
