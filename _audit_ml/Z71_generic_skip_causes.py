# -*- coding: utf-8 -*-
"""Z71: 1866 条通用拒仓的**成因分布**（按文件就地回溯前文，聚合分类）。

方法：对每个含审计通用拒仓行的日志文件，逐行扫描；命中审计行时回看前 12 行，
取其中**最后一条**「拒绝类」日志作为该行原因（优先 risk_engine BLOCK / 模拟下单失败 /
V5Gate BLOCK / DecisionPriceGate / TrancheGate / Persistence / BudgetService）。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = [
    ("risk_engine(daily_quota等)", re.compile(r"\[RiskEngine\] BLOCK.*?code=|\[RiskEngine\] BLOCK")),
    ("paper_模拟下单失败", re.compile(r"模拟下单失败")),
    ("V5Gate BLOCK", re.compile(r"\[V5Gate\] BLOCK")),
    ("DecisionPriceGate", re.compile(r"\[DecisionPriceGate\] BLOCK")),
    ("TrancheGate", re.compile(r"\[TrancheGate\].*(耗尽|DOWNSIZE)")),
    ("Persistence", re.compile(r"\[Persistence\]")),
    ("BudgetService", re.compile(r"\[BudgetService\]")),
    ("Agent无策略", re.compile(r"\[Agent独立\].*无 active 策略")),
    ("ChokeGate", re.compile(r"\[MidLongChokeGate\]")),
    ("MIDLONG_PORTFOLIO", re.compile(r"midlong_portfolio_block")),
    ("其他", re.compile(r".*")),
]
AUDIT = "evaluate_and_execute_returned_false"

files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
cause = Counter()
quota_detail = Counter()
total = 0
files_used = []
for p in files:
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        continue
    hits = [i for i, l in enumerate(lines) if AUDIT in l and "MidLongAudit" in l]
    if not hits:
        continue
    files_used.append((p.name, len(hits)))
    for i in hits:
        total += 1
        picked = "（前文无拒绝日志）"
        quota = ""
        for j in range(i - 1, max(-1, i - 13), -1):
            line = lines[j]
            m = re.search(r"(\d+)/(\d+) 已用尽", line)
            if m:
                quota = f"{m.group(1)}/{m.group(2)}"
            for name, rx in PATTERNS:
                if name == "其他":
                    continue
                if rx.search(line):
                    picked = name
                    break
            if picked != "（前文无拒绝日志）":
                break
        cause[picked] += 1
        if quota:
            quota_detail[quota] += 1

print(f"扫描 {len(files_used)} 个含审计行的日志文件，共 {total} 条通用拒仓")
print("\n成因分布:")
for k, v in cause.most_common():
    print(f"  {v:>6}  {v / max(1, total):>6.1%}  {k}")
print("\n配额 used/limit 明细（来自日志原文）:", dict(quota_detail))
print("\n按文件（前 12）:")
for name, n in sorted(files_used, key=lambda x: -x[1])[:12]:
    print(f"  {n:>6}  {name}")
