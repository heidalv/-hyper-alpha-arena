# -*- coding: utf-8 -*-
"""Z65: 1866 条通用拒仓的真实成因——按日志标签计数（限定 09-08~09-10 窗口）。"""
from __future__ import annotations
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
cands = sorted([p for p in (ROOT / "logs").glob("backend*.log")], key=lambda p: p.stat().st_mtime)
print("候选日志:", [(p.name, round(p.stat().st_size/1e6, 1)) for p in cands])
tags = {
    "V5Gate BLOCK": r"\[V5Gate\] BLOCK",
    "V5Gate PASS": r"\[V5Gate\] PASS",
    "Persistence拦截": r"\[Persistence\].*拦截",
    "Agent独立无策略": r"\[Agent独立\].*无 active 策略",
    "BudgetService满": r"\[BudgetService\].*预算已满",
    "TrancheGate耗尽": r"\[TrancheGate\].*tranche 已耗尽",
    "DecisionPrice BLOCK": r"\[DecisionPriceGate\] BLOCK",
    "LiveDust": r"\[LiveDust\]",
    "Live宪法拦": r"live_risk_block|\[Live宪法\]",
    "midlong_executor拒": r"\[MidLongExecutor\]|\[MidLongExec\]",
    "audit通用拒仓": r"MidLongAudit\] skip.*evaluate_and_execute_returned_false",
    "thesis门(watch)": r"skip open .*reason=watch:",
    "熔断learned": r"midlong_circuit|learned",
}
for p in cands[-2:]:
    c = Counter()
    first = last = None
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            if first is None:
                first = line[:32]
            last = line[:32]
            for name, rx in tags.items():
                if re.search(rx, line):
                    c[name] += 1
    print(f"\n=== {p.name}  首={first}  末={last} ===")
    for name, n in c.most_common():
        print(f"   {n:>7}  {name}")
