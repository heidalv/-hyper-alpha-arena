"""h601 — 文档内部引用的**完整性体检**（只读，R155）。

理由：总账与验收文档今天被反复编辑 ✗，`§1.1`/`§5`/`§6.1.1` 这类**内部指引**可能指向
已改名或不存在的小节 ⇒ 读者白找 ✗。本脚本：
  1. 抽出文档里所有实际存在的标题（`#`~`####`）与编号（`## 1.1` 风格）；
  2. 抽出正文里所有 `§x` / `§x.y` 引用；
  3. 报告**指向不存在的目标**的引用（以及"标题存在但从未被引用"的，仅信息性 ✓）。

用法：python scripts/h601_doc_refs.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("研究结论/总账_20260929.md", "研究结论/三件事验收_20260929.md",
        "研究结论/误删事故_20260929.md")


def main() -> int:
    bad_total = 0
    for rel in DOCS:
        p = ROOT / rel
        if not p.exists():
            print(f"✗ 缺文件 {rel}")
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        heads = set()
        for m in re.finditer(r"^(#{1,4})\s+(.*)$", txt, re.M):
            title = m.group(2).strip()
            heads.add(title)
            num = re.match(r"^([0-9]+(?:\.[0-9]+)*)", title)
            if num:
                heads.add(num.group(1))
        refs = set(re.findall(r"§\s*([0-9]+(?:\.[0-9]+)*)", txt))
        # [R155 修] 首版把 `§7.5`（= §7 里的**第 5 条**）与跨文档引用判成"失效" ✗
        # ⇒ 误报。现改为：只要**顶级编号**存在就算有效 ✓（子编号可能是条目号 ✓）；
        # 另外把"跨文档引用"单列（本脚本不校验另一份文档 ✓）。
        # [R155b 修] **跨文档引用**：若 `§` 前 30 字内出现文档名（如"总账"、"误删事故"），
        # 该引用指的是**另一份文档** ⇒ 不应按本文档的标题判 ✗。
        CROSS = ("总账", "误删事故", "三件事验收", "手续费根因", "停摆事故")
        tops = {h.split(".")[0] for h in heads if h and h[0].isdigit()}
        refs_all = list(re.finditer(r"§\s*([0-9]+(?:\.[0-9]+)*)", txt))
        refs = {m.group(1) for m in refs_all}
        cross = {m.group(1) for m in refs_all
                 if any(c in txt[max(0, m.start() - 30):m.start()] for c in CROSS)}
        bad = sorted(r for r in refs
                     if r.split(".")[0] not in tops and r not in cross)
        itemish = sorted(r for r in refs if "." in r and r.split(".")[0] in tops)
        print("=" * 88)
        print(f"{p.name}：标题 {len(heads)} 个、内部引用 {len(refs)} 个")
        print("=" * 88)
        print(f"  引用的目标：{', '.join('§' + r for r in sorted(refs))}")
        if cross:
            print(f"  · 跨文档引用（指向另一份文档 ⇒ 本检查器不判死）："
                  f"{', '.join('§' + x for x in sorted(cross))}")
        if itemish:
            print(f"  · 含子编号（可能是**条目号**）："
                  f"{', '.join('§' + x for x in itemish)}")
        if bad:
            bad_total += len(bad)
            print(f"  ✗ 顶级编号就不存在（{len(bad)}）：{', '.join('§' + b for b in bad)}")
        else:
            print("  ✓ 所有引用的顶级编号都存在 ✓")
    print("=" * 88)
    print(f"⇒ {'有 ' + str(bad_total) + ' 处引用失效 ✗' if bad_total else '全部有效 ✓'}")
    return 1 if bad_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
