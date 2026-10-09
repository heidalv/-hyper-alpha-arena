# -*- coding: utf-8 -*-
"""根因查询：慢请求是"端点自身慢"还是"进程被全局卡住"？

方法：取最慢的 N 条 `SLOW` 请求，把**它们发生的那一秒**前后（±3s）日志里
"重活"类事件抽出来看（因子计算 / K 线聚合 / 回测 / 扫描 / LLM 等）。
若慢请求的时间点频繁与重活重合 ⇒ 进程级阻塞（GIL/事件循环饥饿）；
若与重活无关 ⇒ 端点自身问题。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "logs" / "backend.log").read_text(
    encoding="utf-8", errors="replace").splitlines()

# 带时间戳的行 → (epoch, 原文)
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
stamped = []
for ln in lines:
    m = TS.match(ln)
    if m:
        try:
            stamped.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), ln))
        except ValueError:
            continue

slow = []
for ln in lines:
    m = re.search(r"SLOW\s+([\d.]+)s\s+(\w+)\s+(\S+)", ln)
    t = TS.match(ln)
    if m and t:
        slow.append((float(m.group(1)),
                     datetime.strptime(t.group(1), "%Y-%m-%d %H:%M:%S"),
                     m.group(2), m.group(3)))
print(f"SLOW 样本 = {len(slow)}")

HEAVY = {
    "因子计算": "factor_calculator",
    "因子加载": "FactorLoader",
    "K线聚合": "KlineAgg",
    "回测": "backtest",
    "进化/GA": "evolution",
    "扫描": "MarketScanner",
    "LLM流式": "[LLM sync",
    "质量修复": "quality_repair",
    "深回填": "DepthBackfill",
    "宏观采集": "MacroCollector",
    "学习循环": "learning_loop",
    "MLTO周期": "mlto_cycle",
    "K线修复": "kline_repair",
}

# 全窗口基线：各重活出现频率（每分钟）
span_min = max(1.0, (stamped[-1][0] - stamped[0][0]).total_seconds() / 60.0) if stamped else 1.0
base = Counter()
for _, ln in stamped:
    for k, p in HEAVY.items():
        if p in ln:
            base[k] += 1
print(f"\n全窗口跨度 ≈ {span_min:.0f} 分钟；各重活出现频率（次/分钟）：")
for k, v in base.most_common():
    print(f"  {k:10s} {v:7d}  ({v/span_min:.2f}/分)")

# 对最慢 25 条，看 ±3s 内有没有重活
top = sorted(slow, reverse=True)[:25]
print("\n最慢 25 条请求，其 ±3s 窗口内的重活：")
hit = Counter()
for dur, t, meth, path in top:
    near = [ln for tt, ln in stamped if abs((tt - t).total_seconds()) <= 3]
    kinds = {k for k, p in HEAVY.items() if any(p in ln for ln in near)}
    for k in kinds:
        hit[k] += 1
    print(f"  {dur:6.1f}s {t:%H:%M:%S} {meth} {path.split('?')[0][:38]:38s} 同期重活={sorted(kinds) or '无'}")
print("\n按重活类型统计（最慢 25 条中有多少条同期出现）：")
for k, v in hit.most_common():
    print(f"  {k:10s} {v}/25")
