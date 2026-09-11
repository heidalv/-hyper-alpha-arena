# -*- coding: utf-8 -*-
"""Z83: V5Gate PASS 的 nature 分布 + EV 闸「跳过」是否有发生（debug 不可见 → 需静态核验）。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rx_pass = re.compile(r"\[V5Gate\] PASS symbol=(\S+) action=(\S+) conf=(\S+) nature=(\S+)")
rx_skip = re.compile(r"\[MidLongEvGate\] 跳过")
by_nature = Counter()
by_day_nature = Counter()
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
seen = set()
skips = 0
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            if "[V5Gate] PASS" in line:
                m = rx_pass.search(line)
                if m:
                    key = line.strip()[:160]
                    if key not in seen:
                        seen.add(key)
                        by_nature[m.group(4)] += 1
                        d = re.match(r"^(\d{4}-\d{2}-\d{2})", line)
                        if d:
                            by_day_nature[f"{d.group(1)}|{m.group(4)}"] += 1
            elif rx_skip.search(line):
                skips += 1
print("V5Gate PASS 按 nature:", dict(by_nature))
print("\n按天×nature（近 12 天）:")
for k, v in sorted(by_day_nature.items()):
    if k[:10] >= "2026-08-30":
        print(f"   {k}  {v}")
print("\n[MidLongEvGate] 跳过（异常被吞）出现次数:", skips)
