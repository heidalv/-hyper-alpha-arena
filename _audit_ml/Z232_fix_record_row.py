# -*- coding: utf-8 -*-
"""[§82 恢复 2026-09-11] 修 §62 的 ⚪ 记录 行标记，并对比「解析到 vs 文件里」的 ID 集合。"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
P = ROOT / "_中长线负期望根因报告_20260909.md"
BAD = "\ufffd"

text = P.read_text(encoding="utf-8")
lines = text.splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
           if l.startswith("## 6") and not l.startswith("## 62."))

# ① 修 #15 的严重度标记（⚪ emoji 被破坏）
fixed = 0
for i in range(start, end):
    l = lines[i]
    if re.match(rf"^\| 15 \| [{BAD}?]+\s*记录", l):
        lines[i] = re.sub(rf"^\| 15 \| [{BAD}?]+\s*记录", "| 15 | ⚪ 记录", l)
        fixed += 1
print("② 修 #15 严重度标记:", fixed, "处")
if fixed:
    bak = P.with_name(P.name + f".pre_whitelist_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(P, bak)
    P.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("   已备份:", bak.name)

# ② 文件里可见的 ID（§62 范围内，4 列或 5 列表行）
file_ids = []
for i in range(start, end):
    l = lines[i]
    if not l.startswith("|") or set(l.strip()) <= set("|-: "):
        continue
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    if not cells:
        continue
    rid = cells[0]
    if re.fullmatch(r"\d+", rid):
        file_ids.append(rid)
print("文件里数字 ID 行数:", len(file_ids))
print("  重复 ID:", [x for x in set(file_ids) if file_ids.count(x) > 1])
print("  ID 列表:", ",".join(file_ids))

# ③ 校验器解析到的 ID
spec = importlib.util.spec_from_file_location(
    "adi", ROOT / "backend" / "scripts" / "audit_defect_inventory.py")
adi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adi)
rows = adi.parse_inventory() if hasattr(adi, "parse_inventory") else None
if rows is None:
    for name in dir(adi):
        if "parse" in name.lower() or "collect" in name.lower():
            print("  可用解析函数:", name)
else:
    parsed = [str(r.get("id")) for r in rows]
    print("校验器解析行数:", len(parsed))
    print("  文件有、解析无:", sorted(set(file_ids) - set(parsed), key=lambda x: int(x)))
    print("  解析有、文件无:", sorted(set(parsed) - set(file_ids), key=lambda x: int(x) if x.isdigit() else 0))
