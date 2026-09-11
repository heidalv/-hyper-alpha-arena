# -*- coding: utf-8 -*-
"""Z73: §52.6-C/D 真实数据 —— 「无 active 策略」与「TrancheGate 耗尽」的分布。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rx_no_strat = re.compile(r"\[Agent独立\] (\S+) tier=(\S+) 无 active 策略")
rx_tranche = re.compile(r"\[TrancheGate\] (\S+) tier=(\S+) margin_pct=0%")
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2})")

no_strat = Counter()
no_strat_day = Counter()
tranche = Counter()
tranche_day = Counter()
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            m = rx_no_strat.search(line)
            if m:
                no_strat[f"{m.group(1)}/{m.group(2)}"] += 1
                d = rx_ts.match(line)
                if d:
                    no_strat_day[d.group(1)] += 1
                continue
            m = rx_tranche.search(line)
            if m:
                tranche[f"{m.group(1)}/{m.group(2)}"] += 1
                d = rx_ts.match(line)
                if d:
                    tranche_day[d.group(1)] += 1

print("=== [Agent独立] 无 active 策略 ===")
print("  总计:", sum(no_strat.values()))
print("  按 (symbol/tier) top15:", dict(no_strat.most_common(15)))
print("  按日期:", dict(sorted(no_strat_day.items())))

print("\n=== [TrancheGate] margin_pct=0%（耗尽）===")
print("  总计:", sum(tranche.values()))
print("  按 (symbol/tier) top15:", dict(tranche.most_common(15)))
print("  按日期:", dict(sorted(tranche_day.items())))
