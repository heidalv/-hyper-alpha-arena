# -*- coding: utf-8 -*-
"""Z82: EV 闸是否已开始真的拦单？——日志里 [EVGate] 拒绝的出现与日期分布。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2})")
pats = {
    "V5Gate BLOCK 带 [EVGate]": re.compile(r"\[V5Gate\] BLOCK.*\[EVGate\]"),
    "EVGate 拦截(直接日志)": re.compile(r"期望值不足拦截"),
    "BLOCK 行(EV 闸)": re.compile(r"\[MidLongEvGate\] BLOCK"),
    "EV= 出现在任何行": re.compile(r"\[EVGate\] EV="),
}
counts = {k: Counter() for k in pats}
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
seen = set()
samples = {}
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            key = line.strip()[:200]
            if key in seen:
                continue
            for name, rx in pats.items():
                if rx.search(line):
                    seen.add(key)
                    m = rx_ts.match(line)
                    counts[name][m.group(1) if m else "?"] += 1
                    samples.setdefault(name, line.strip()[:200])
                    break
for name, c in counts.items():
    print(f"\n=== {name} ===")
    print("  合计:", sum(c.values()), " 按日:", dict(sorted(c.items())))
    if name in samples:
        print("  样本:", samples[name])
