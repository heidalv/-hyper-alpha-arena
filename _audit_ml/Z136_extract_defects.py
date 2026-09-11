# -*- coding: utf-8 -*-
"""Z136: 从报告里抽取全部缺陷条目（供 §62 合并清单核对，避免遗漏）。

规则：markdown 表格行且形如 `| <数字> | <严重度 emoji> ...`；记录其所属最近的一级/三级标题。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rep = ROOT / "_中长线负期望根因报告_20260909.md"
lines = rep.read_text(encoding="utf-8", errors="ignore").splitlines()

SEV = {"🔴": "高", "🟠": "中", "🟡": "低", "⚪": "记录"}
ROW = re.compile(r"^\|\s*~{0,2}(\d+)\s*~{0,2}\s*\|\s*(🔴|🟠|🟡|⚪)\s*([^|]*)\|(.*)$")
HEAD = re.compile(r"^(#{2,3})\s+(\S+)")

rows = []
head = ""
for ln in lines:
    mh = HEAD.match(ln)
    if mh:
        head = ln.strip()
        continue
    m = ROW.match(ln)
    if m:
        rows.append({"section": head[:40], "id": int(m.group(1)), "sev": SEV[m.group(2)],
                     "title": m.group(3).strip()[:90], "rest": m.group(4).strip()})

print(f"抽到缺陷行 {len(rows)} 条")
by_sev = Counter(r["sev"] for r in rows)
print("严重度分布:", dict(by_sev))
print("\n按章节:")
for s, n in Counter(r["section"] for r in rows).most_common():
    print(f"  {n:>3}  {s}")

print("\n=== 高/中 条目（用于合并清单）===")
for r in rows:
    if r["sev"] in ("高", "中"):
        status = "已修" if "已修" in r["rest"] else (
            "待决策" if ("待决策" in r["rest"] or "待你拍板" in r["rest"] or "待拍板" in r["rest"])
            else "其它")
        print(f"  [{r['sev']}] #{r['id']} {r['title']}")
        print(f"        状态={status} | {r['rest'][:150]}")
