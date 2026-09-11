# -*- coding: utf-8 -*-
"""Z68: 找到含审计通用拒仓日志的文件，并打印其上下文（确定真实原因）。"""
from __future__ import annotations
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
needles = ["evaluate_and_execute_returned_false", "[MidLongAudit] skip"]
files = [p for p in (ROOT / "logs").rglob("*.log") if p.stat().st_size > 200_000]
cnt = Counter()
where = {}
for p in files:
    try:
        with p.open(encoding="utf-8", errors="ignore") as f:
            c = 0
            for line in f:
                if needles[0] in line:
                    c += 1
                    if c == 1:
                        where[p.name] = line.strip()[:180]
            if c:
                cnt[p.name] = c
    except Exception:
        pass
print("含通用拒仓审计日志的文件:")
for name, c in cnt.most_common():
    print(f"  {c:>6}  {name}")
    print(f"        首行: {where.get(name)}")
print("\n（若为空：审计行只写 JSONL，未进任何日志文件）")
