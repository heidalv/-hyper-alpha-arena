# -*- coding: utf-8 -*-
"""量化：慢请求是否随"进程内重活强度"上升？（相关系数）

分桶 5 分钟，统计每桶：
  · SLOW 条数（后端 slowapi ≥3s）
  · 因子计算行数（factor_calculator：CPU 重活）
  · LLM 流式行数
  · 出进程分析（子进程，**不该**占本进程 CPU）作为对照
然后算 Pearson 相关。若 SLOW 与 CPU 重活强相关 ⇒ 进程级争用是根因。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "logs" / "backend.log").read_text(
    encoding="utf-8", errors="replace").splitlines()
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

BUCKET = timedelta(minutes=5)
slow_b, fac_b, llm_b, sub_b, rows_b = Counter(), Counter(), Counter(), Counter(), Counter()
ts_list = []
for ln in lines:
    m = TS.match(ln)
    if not m:
        continue
    try:
        t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        continue
    k = t.replace(minute=(t.minute // 5) * 5, second=0)
    ts_list.append(t)
    rows_b[k] += 1
    if "SLOW" in ln and re.search(r"SLOW\s+[\d.]+s", ln):
        slow_b[k] += 1
    if "factor_calculator" in ln:
        fac_b[k] += 1
    if "[LLM sync" in ln:
        llm_b[k] += 1
    if "出进程分析" in ln:
        sub_b[k] += 1

keys = sorted(rows_b)
print(f"窗口 {keys[0]} → {keys[-1]}   分桶数 = {len(keys)}")


def pearson(a, b):
    n = len(a)
    if n < 3:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else float("nan")


S = [slow_b[k] for k in keys]
F = [fac_b[k] for k in keys]
L = [llm_b[k] for k in keys]
P = [sub_b[k] for k in keys]
R = [rows_b[k] for k in keys]
print(f"\nSLOW 总计 {sum(S)}  因子计算 {sum(F):,}  LLM {sum(L):,}  出进程 {sum(P)}  日志 {sum(R):,}")
print("\n相关系数（5 分钟桶，n=%d）:" % len(keys))
print(f"  SLOW vs 因子计算      r = {pearson(S, F):+.3f}")
print(f"  SLOW vs LLM 流式      r = {pearson(S, L):+.3f}")
print(f"  SLOW vs 出进程分析    r = {pearson(S, P):+.3f}   （对照：子进程不吃本进程 CPU）")
print(f"  SLOW vs 总日志行数    r = {pearson(S, R):+.3f}   （总强度的粗代理）")

print("\n最忙的 10 个 5 分钟桶（按因子计算量）：")
print(f"  {'桶':16s} {'因子计算':>9s} {'LLM':>7s} {'SLOW':>5s} {'日志行':>8s}")
for k in sorted(keys, key=lambda x: -fac_b[x])[:10]:
    print(f"  {k:%H:%M}          {fac_b[k]:9d} {llm_b[k]:7d} {slow_b[k]:5d} {rows_b[k]:8d}")

print("\n有 SLOW 的桶（前 12，按 SLOW 数）：")
print(f"  {'桶':16s} {'SLOW':>5s} {'因子计算':>9s} {'LLM':>7s}")
for k in sorted([k for k in keys if slow_b[k]], key=lambda x: -slow_b[x])[:12]:
    print(f"  {k:%H:%M}          {slow_b[k]:5d} {fac_b[k]:9d} {llm_b[k]:7d}")
