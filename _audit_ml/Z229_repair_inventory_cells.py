# -*- coding: utf-8 -*-
"""[§82 事故恢复 2026-09-11] 修复 §62 清单**机器可读单元**：严重度词 + 状态标记。

背景：PowerShell 重编码事故把大量字符变成 U+FFFD（读作 `\ufffd`，常与残留的 `?` 相邻），
其中**严重度词**（高/中/低/记录）与**状态标记**（✅/🟡/❌/📋/⏳）被破坏 ⇒
`audit_defect_inventory.py` 的分档统计与本报告 §62.1 统计行不一致（测试会红）。

本脚本（就地重写，逐行最小改动）：
  1. 严重度单元按**存活的 emoji** 规范化为 `🔴 高` / `🟠 中` / `🟡 低` / `⚪ 记录`；
  2. 状态单元开头的损坏标记按存活 emoji/关键词规范化；
  3. 逐块统计行数并与块标题声称的条数对比，指出缺行/多行。
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
BAD = "\ufffd"

SEV = {"🔴": "高", "🟠": "中", "🟡": "低", "⚪": "记录"}
SEV_EMOJI = {"高": "🔴", "中": "🟠", "低": "🟡", "记录": "⚪"}
# 状态标记：损坏后常见形态为 `\ufffd?` / `\ufffd` / `\ufffd\ufffd?`
MARK = {
    "✅": re.compile(rf"^[{BAD}?]+"),
    "🟡": re.compile(rf"^[{BAD}?]+"),
    "❌": re.compile(rf"^[{BAD}?]+"),
    "📋": re.compile(rf"^[{BAD}?]+"),
    "⏳": re.compile(rf"^[{BAD}?]+"),
}


def norm_sev(cell: str) -> str:
    for e, w in SEV.items():
        if e in cell:
            rest = cell.replace(e, "").replace(BAD, "").replace("?", "").strip()
            rest = rest if rest and len(rest) <= 6 else ""
            return f"{e} {w}" + (f" {rest}" if rest else "")
    return cell


def norm_status(cell: str) -> str:
    s = cell
    # 已修/部分/撤销/待决策/待办/记录 关键词优先（关键词大多存活）
    lead = ""
    if "已修" in s or "已加" in s or "已补" in s or "已订正" in s:
        lead = "✅ 已修"
    elif "部分" in s:
        lead = "🟡 部分"
    elif "撤销" in s or "误报" in s:
        lead = "❌ 撤销"
    elif "待决策" in s:
        lead = "📋 待决策"
    elif "待办" in s:
        lead = "⏳ 待办"
    elif re.match(rf"^[{BAD}?]+$", s.strip()):
        return s
    if not lead:
        return s
    # 去掉原有（可能损坏的）标记，保留其余文本
    body = re.sub(rf"^[{BAD}?✅🟡❌📋⏳\s]*", "", s)
    body = re.sub(r"^(已修|部分|撤销|待决策|待办)\s*", "", body)
    return f"{lead}：{body}" if body else lead


def main() -> int:
    text = P.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("## 62. "))
    end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
               if l.startswith("## 6") and not l.startswith("## 62."))
    changed = 0
    block = ""
    blocks = {}
    for i in range(start, end):
        l = lines[i]
        m = re.match(r"^### 62\.\d .*?（(\d+) 条）", l)
        if m:
            block = l[:20]
            blocks[block] = {"claimed": int(m.group(1)), "rows": 0}
        if l.startswith("| ") and l.count("|") >= 6:
            cells = [c.strip() for c in l.strip().strip("|").split("|")]
            if len(cells) >= 5 and not set(cells[0]) <= set("-: ") and cells[0] != "ID":
                if block in blocks:
                    blocks[block]["rows"] += 1
                new = list(cells)
                new[1] = norm_sev(cells[1])
                new[-1] = norm_status(cells[-1])
                if new != cells:
                    lines[i] = "| " + " | ".join(new) + " |"
                    changed += 1
    bak = P.with_name(P.name + f".pre_repair_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(P, bak)
    P.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("已备份:", bak.name)
    print("规范化单元行数:", changed)
    print("分块核对（声称 vs 实际行数）：")
    for b, d in blocks.items():
        flag = "✅" if d["claimed"] == d["rows"] else "❗"
        print(f"  {flag} {b}  声称 {d['claimed']} / 实际 {d['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
