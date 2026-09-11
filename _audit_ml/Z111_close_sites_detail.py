# -*- coding: utf-8 -*-
"""Z111: 逐个核验 7 个「CLOSE-ish」调用点的真实语义（打印所在函数名 + 事件名 + 前后文）。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "backend/services/full_auto/master_execution.py"
src = p.read_text(encoding="utf-8", errors="ignore")
lines = src.splitlines()

# 函数边界
tree = ast.parse(src)
funcs = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        funcs.append((node.lineno, node.end_lineno, node.name))


def _owner(ln: int) -> str:
    best = "?"
    for a, b, n in funcs:
        if a <= ln <= b and (b - a) < 8000:
            best = n
    return best


for ln in (1640, 1660, 1945, 2341, 2442, 2535, 2599):
    i = ln - 1
    # 找该站点最近的 append_event 事件名
    ev = ""
    for j in range(i, min(len(lines), i + 8)):
        m = re.search(r'append_event\(\s*session,\s*"([^"]+)"', lines[j])
        if m:
            ev = m.group(1)
            break
    for j in range(i, max(0, i - 60), -1):
        m = re.search(r'append_event\(\s*session,\s*"([^"]+)"', lines[j])
        if m and not ev:
            ev = m.group(1)
        m2 = re.search(r'reason\s*=\s*"([^"]+)"', lines[j])
        if m2:
            ev = f"{ev} reason={m2.group(1)}"
            break
    print(f"\n=== {ln}  在函数 {_owner(ln)}()  事件/原因: {ev} ===")
    for j in range(max(0, i - 5), min(len(lines), i + 4)):
        mark = ">>" if j == i else "  "
        s = lines[j].rstrip()
        if s.strip():
            print(f"  {mark} {s[:150]}")
