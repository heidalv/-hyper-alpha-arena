# -*- coding: utf-8 -*-
"""Z67: 09-08/09-09 窗口的日志覆盖情况（判断关联法为何失败）。"""
from __future__ import annotations
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2})")
files = [p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 500_000]
files += [p for p in (ROOT / "logs").glob("backend.pid*.log") if p.stat().st_size > 500_000]
print(f"{'file':<42}{'MB':>7}  {'first':<12}{'last':<12} 日期计数(09-07..09-10)")
for p in sorted(files, key=lambda x: -x.stat().st_size)[:14]:
    days = Counter()
    first = last = ""
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = rx_ts.match(line)
            if not m:
                continue
            d = m.group(1)
            if not first:
                first = d
            last = d
            if d >= "2026-09-07":
                days[d] += 1
    print(f"{p.name:<42}{round(p.stat().st_size/1e6):>7}  {first:<12}{last:<12} {dict(sorted(days.items()))}")
