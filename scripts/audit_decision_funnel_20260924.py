# -*- coding: utf-8 -*-
"""[R31] 决策漏斗量化：主脑批次 "n 个候选 → 开仓 0" 到底卡在哪。

数据源：logs/brain_subprocess.log（活主脑所在进程的日志，**不是** backend.log —— R25 教训）。
统计三件事：
  1) `[MidLongBrain] batch tier=… n=… watch=… idle=… opened=…` 的批次级 n vs opened；
  2) `skip open … reason=…` 的原因分布（谁在拦）；
  3) `[V5Gate] rule=…` 的规则分布（若存在）。
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LOG = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\brain_subprocess.log")
# 轮转文件一起统计（.1 是最新的一卷）
FILES = [LOG] + [LOG.with_name(LOG.name + f".{i}") for i in (1, 2)]
FILES = [p for p in FILES if p.exists()]

batch_re = re.compile(
    r"batch tier=(\w+) n=(\d+) watch=(\d+) idle=(\d+) opened=(\d+)"
)
skip_re = re.compile(r"skip open (\S+) (\w+) reason=(\S+)")
v5_re = re.compile(r"\[V5Gate\] rule=(\S+)")

per_tier = defaultdict(lambda: {"batches": 0, "n": 0, "opened": 0, "watch": 0, "idle": 0})
skip_reasons = Counter()
v5_rules = Counter()
lines = 0

for p in FILES:
    try:
        with p.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                lines += 1
                m = batch_re.search(line)
                if m:
                    tier, n, w, i, o = m.group(1), *map(int, m.groups()[1:])
                    d = per_tier[tier]
                    d["batches"] += 1
                    d["n"] += n
                    d["watch"] += w
                    d["idle"] += i
                    d["opened"] += o
                m = skip_re.search(line)
                if m:
                    skip_reasons[m.group(3)] += 1
                m = v5_re.search(line)
                if m:
                    v5_rules[m.group(1)] += 1
    except Exception as exc:  # noqa: BLE001
        print(f"读取 {p.name} 失败: {exc}")

print(f"扫描文件: {[p.name for p in FILES]}  总行数={lines}")
print("\n=== 1) 批次级 候选 vs 开仓 ===")
print(f"{'tier':10s} {'批次':>5s} {'候选':>7s} {'开仓':>5s} {'watch':>6s} {'idle':>6s} {'开仓率':>8s}")
for tier, d in sorted(per_tier.items(), key=lambda kv: -kv[1]["n"]):
    rate = (d["opened"] / d["n"] * 100) if d["n"] else 0.0
    print(f"{tier:10s} {d['batches']:5d} {d['n']:7d} {d['opened']:5d} "
          f"{d['watch']:6d} {d['idle']:6d} {rate:7.2f}%")

print("\n=== 2) skip open 原因分布（Top 15）===")
for r, c in skip_reasons.most_common(15):
    print(f"  {r:44s} {c:6d}")
print(f"  (合计 {sum(skip_reasons.values())})")

print("\n=== 3) V5Gate 规则分布（Top 10）===")
if not v5_rules:
    print("  (本窗口无 [V5Gate] 行)")
for r, c in v5_rules.most_common(10):
    print(f"  {r:44s} {c:6d}")
