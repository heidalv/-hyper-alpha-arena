# -*- coding: utf-8 -*-
"""Z168：从**线上日志**统计各模块的 logger 标签（身份）分布。

日志行格式: `2026-09-10 13:16:40 [INFO] [tr=-] <module>:<line> - msg`
若同一模块同时以 `services.x` 与 `backend.services.x` 两种标签出现 ⇒ 该模块在
真实进程中**被实例化了两次**（模块身份分裂的线上证据）。
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
LOG = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log")
# 只读最近 40MB（轮转后活动文件不大，保险起见做上限）
MAX = 40 * 1024 * 1024
size = LOG.stat().st_size
with LOG.open("rb") as f:
    if size > MAX:
        f.seek(size - MAX)
    data = f.read()
text = data.decode("utf-8", errors="replace")

TAG = re.compile(r"\[(?:INFO|WARNING|ERROR|DEBUG|CRITICAL)\]\s*\[tr=[^\]]*\]\s*([\w.]+):\d+")
pairs: dict[str, set[str]] = defaultdict(set)
counts: Counter[str] = Counter()
for m in TAG.finditer(text):
    mod = m.group(1)
    counts[mod] += 1
    short = mod[8:] if mod.startswith("backend.") else mod
    pairs[short].add("backend" if mod.startswith("backend.") else "toplevel")

dual = {k: v for k, v in pairs.items() if len(v) > 1}
print(f"日志中出现过的模块标签: {len(pairs)} 个；其中**双身份**出现的: {len(dual)} 个")
print("\n=== 双身份模块（同一模块两套身份都在跑）===")
for k in sorted(dual):
    tl = counts.get(k, 0)
    pk = counts.get("backend." + k, 0)
    print(f"  {k:58s} 顶格 {tl:6d} 行 / backend {pk:6d} 行")

print("\n=== 关注模块的身份分布（修正口径：pairs 的键是去掉 backend. 前缀的全名）===")
for short in sorted(pairs):
    if any(k in short for k in ("onchain", "scheduler", "decay_monitor", "learning.backend_registry",
                                "derivatives_analytics", "price_cache", "market_flow_collector",
                                "signal_detection", "kline_cache", "unified_data_pool", "startup")):
        print(f"  {short:58s} {'双身份 ❌' if short in dual else '单一身份 ✅'}"
              f"   顶格={counts.get(short, 0)} backend={counts.get('backend.' + short, 0)}")
print("\n  未在日志中出现（无法据此判定）:", ", ".join(
    m for m in ["price_cache", "market_flow_collector", "signal_detection_service",
                "kline_cache_service", "unified_data_pool", "startup"]
    if not any(k == m for k in pairs)))
