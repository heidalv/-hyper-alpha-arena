# -*- coding: utf-8 -*-
"""[R31] 决策漏斗量化（第二部分）：真实开仓/拦截发生在 API 进程。

纠正：`brain_subprocess.log` 里 `batch … opened=0` 是**设计如此**
（brain.py:2605「出进程主脑：重分析拆独立子进程，API 进程只做轻开仓」），
不能当成"不开仓"。真正要统计的是 API 进程日志（backend.log / backend.pid*.log）里的：
  - `skip open <sym> <tier> reason=<…>` 的 reason 分布（谁在拦）
  - `小仓试探`（probe entry）次数
  - `[V5Gate] rule=<…>`（若存在）
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

LOGS = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs")
FILES = [LOGS / "backend.log"] + sorted(
    LOGS.glob("backend.pid*.log"), key=lambda p: p.stat().st_mtime, reverse=True
)

skip_re = re.compile(r"skip open (\S+) (\w+) reason=(\S+)")
probe_re = re.compile(r"小仓试探 (\S+) (\w+)")
v5_re = re.compile(r"\[V5Gate\] rule=(\S+)")
opened_re = re.compile(r"\bopened=(\d+)")

reasons = Counter()
tiers_of_skip = Counter()
probes = Counter()
v5 = Counter()
lines = 0

for p in FILES:
    try:
        with p.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                lines += 1
                m = skip_re.search(line)
                if m:
                    reasons[m.group(3)] += 1
                    tiers_of_skip[m.group(2)] += 1
                m = probe_re.search(line)
                if m:
                    probes[m.group(2)] += 1
                m = v5_re.search(line)
                if m:
                    v5[m.group(1)] += 1
    except Exception as exc:  # noqa: BLE001
        print(f"读取 {p.name} 失败: {exc}")

print(f"扫描 {len(FILES)} 个 API 日志文件，总行数={lines}")
print(f"  文件: {[p.name for p in FILES]}")

print("\n=== 1) skip open 原因分布（Top 20）===")
for r, c in reasons.most_common(20):
    print(f"  {r:52s} {c:6d}")
print(f"  合计 {sum(reasons.values())}")

print("\n=== 2) skip open 的 tier 分布 ===")
for t, c in tiers_of_skip.most_common():
    print(f"  {t:8s} {c:6d}")

print("\n=== 3) 「小仓试探」次数（按 tier）===")
for t, c in probes.most_common():
    print(f"  {t:8s} {c:6d}")
print(f"  合计 {sum(probes.values())}")

print("\n=== 4) V5Gate 规则分布（Top 10）===")
if not v5:
    print("  (无)")
for r, c in v5.most_common(10):
    print(f"  {r:52s} {c:6d}")
