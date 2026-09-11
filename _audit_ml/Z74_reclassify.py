# -*- coding: utf-8 -*-
"""Z74: 修正 Z71 的归类错误（DOWNSIZE 被误当成拒绝）后，重新统计隐藏拒单成因。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 只保留**真正的拒绝**标记（不含 DOWNSIZE / 缩仓类 INFO）
REJECT = [
    ("risk_engine（配额等）", re.compile(r"\[RiskEngine\] BLOCK")),
    ("paper_模拟下单失败", re.compile(r"模拟下单失败")),
    ("V5Gate BLOCK", re.compile(r"\[V5Gate\] BLOCK")),
    ("DecisionPriceGate BLOCK", re.compile(r"\[DecisionPriceGate\] BLOCK")),
    ("无 active 策略", re.compile(r"无 active 策略")),
    ("Persistence 未达tick", re.compile(r"\[Persistence\].*拦截")),
    ("BudgetService 预算满", re.compile(r"\[BudgetService\].*预算")),
    ("ChokeGate 拒单", re.compile(r"\[MidLongChokeGate\]")),
    ("组合闸 portfolio_block", re.compile(r"midlong_portfolio_block")),
    ("TrancheGate 耗尽", re.compile(r"\[TrancheGate\].*margin_pct=0%")),
]
AUDIT = "evaluate_and_execute_returned_false"

files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
cause = Counter()
total = 0
for p in files:
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        continue
    hits = [i for i, l in enumerate(lines) if AUDIT in l and "MidLongAudit" in l]
    for i in hits:
        total += 1
        picked = "（前文 12 行内无拒绝日志）"
        for j in range(i - 1, max(-1, i - 13), -1):
            line = lines[j]
            for name, rx in REJECT:
                if rx.search(line):
                    picked = name
                    break
            if picked != "（前文 12 行内无拒绝日志）":
                break
        cause[picked] += 1

print(f"通用拒仓总数 {total}（修正归类后）")
for k, v in cause.most_common():
    print(f"  {v:>6}  {v / max(1, total):>6.1%}  {k}")
