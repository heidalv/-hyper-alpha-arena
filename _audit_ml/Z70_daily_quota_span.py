# -*- coding: utf-8 -*-
"""Z70: daily_quota 拦截的时间跨度（是否按日重置？）。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rx = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}).*daily_quota\[total\] (\d+)/(\d+)")
by_day = Counter()
by_hour = Counter()
pairs = Counter()
first = last = None
files = sorted(
    [p for p in (ROOT / "logs").glob("backend*.log") if p.stat().st_size > 100_000],
    key=lambda p: p.stat().st_mtime,
)
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            m = rx.search(line)
            if not m:
                continue
            day, hh, mm, used, lim = m.groups()
            if day < "2026-09-01":
                continue
            by_day[day] += 1
            by_hour[f"{day} {hh}"] += 1
            pairs[f"{used}/{lim}"] += 1
            key = f"{day} {hh}:{mm}"
            first = first or key
            last = key

print("daily_quota[total] 拦截分布（09-01 起）:")
for d, n in sorted(by_day.items()):
    print(f"   {d}  {n}")
print("\nused/limit 组合:", dict(pairs))
print("\n首/末:", first, "/", last)
print("\n按小时（前 30）:")
for k, v in sorted(by_hour.items())[:30]:
    print(f"   {k}  {v}")
print("\n按小时（后 20）:")
for k, v in sorted(by_hour.items())[-20:]:
    print(f"   {k}  {v}")
