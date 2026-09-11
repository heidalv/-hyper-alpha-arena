# -*- coding: utf-8 -*-
"""Z84: swing 的 V5Gate PASS 具体形态（action 是什么？同 tick 有 EV 闸记录吗？）"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RX = re.compile(r"\[V5Gate\] PASS symbol=(\S+) action=(\S+) conf=(\S+) nature=(\S+)")
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
swing_samples = []
tf_samples = []
seen = set()
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            m = RX.search(line)
            if not m:
                continue
            key = line.strip()[:160]
            if key in seen:
                continue
            seen.add(key)
            nat = m.group(4)
            if nat == "swing" and len(swing_samples) < 8:
                swing_samples.append(line.strip()[:190])
            elif nat == "trend_follow" and len(tf_samples) < 4:
                tf_samples.append(line.strip()[:190])

print("=== nature=swing 的 V5Gate PASS 样本 ===")
for s in swing_samples:
    print("   ", s)
print("\n=== nature=trend_follow 的 V5Gate PASS 样本 ===")
for s in tf_samples:
    print("   ", s)

# swing PASS 的 action 分布
from collections import Counter  # noqa: E402
act = Counter()
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            m = RX.search(line)
            if m and m.group(4) == "swing":
                act[m.group(2)] += 1
print("\nswing PASS 的 action 分布:", dict(act))
