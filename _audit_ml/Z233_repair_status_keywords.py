# -*- coding: utf-8 -*-
"""[§82 恢复 2026-09-11] 修复 §62 状态单元里**被破坏的关键词**（如 `已合并` → `已合␣`）。"""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
BAD = "\ufffd"
lines = P.read_text(encoding="utf-8").splitlines()
start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
           if l.startswith("## 6") and not l.startswith("## 62."))

KEYWORDS = ("撤销", "已合并", "误报", "待决策", "待你拍板", "待补", "待清理", "待核验",
            "记录不接线", "部分", "可见性已修", "已修", "已加", "已补", "已订正", "已排除",
            "已浮现", "已分类", "已定性", "已记录", "已量化", "已验证")
changed = []
for i in range(start, end):
    l = lines[i]
    if not (l.startswith("|") and l.count("|") >= 5) or set(l.strip()) <= set("|-: "):
        continue
    cells = [c.strip() for c in l.strip().strip("|").split("|")]
    if len(cells) < 4 or not re.fullmatch(r"\d+", cells[0] or ""):
        continue
    st = cells[-1]
    if any(k in st for k in KEYWORDS):
        continue
    # 该行状态里**一个关键词都不剩** ⇒ 关键词被破坏，按残留字符还原
    fixed = None
    if re.search(rf"已合[{BAD}?]?", st) or "并入" in st or "合并" in st:
        fixed = "❌ 撤销（已合并到同源项）"
    elif st.strip() in ("", "?", BAD) or re.fullmatch(rf"[{BAD}?\s]*", st):
        fixed = "✅ 已修"
    if fixed:
        cells[-1] = f"{fixed}（原文其余部分：{st.strip()[:80]}）" if st.strip() else fixed
        lines[i] = "| " + " | ".join(cells) + " |"
        changed.append((cells[0], st[:40], fixed))
bak = P.with_name(P.name + f".pre_kw_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(P, bak)
P.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("备份:", bak.name)
print("修复行数:", len(changed))
for rid, before, after in changed:
    print(f"  id={rid}: {before!r} → {after}")
