# -*- coding: utf-8 -*-
"""Z132: 净敞口拦截的 `after_pct` 分布 —— 是"书满了"还是"新单名义估得过大"？

拦截文案格式：`midlong_portfolio_block:net_exposure {after}%>{cap}% (after SYM long)`。
若 after 远大于 cap、而当时账面敞口很低，说明**新单名义被高估**（估到几百 % 权益），
闸就会长期误拦——这是典型的"闸用错输入"，而不是真的满仓。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.mlto.midlong_direction_audit import _iter_rows  # noqa: E402

RX = re.compile(r"net_exposure ([\d.]+)%>([\d.]+)%(?: \(after (\w+))?")

buckets = Counter()
ratio_by_day = {}
recent = []
n = 0
for row in _iter_rows():
    m = RX.search(str(row.get("reason") or ""))
    if not m:
        continue
    n += 1
    after = float(m.group(1))
    b = ("<100" if after < 100 else "100-200" if after < 200 else
         "200-500" if after < 500 else "500-1000" if after < 1000 else
         "1000-5000" if after < 5000 else ">5000")
    buckets[b] += 1
    ts = float(row.get("epoch") or 0)
    if ts:
        recent.append((ts, after, m.group(3), str(row.get("symbol") or "?")))
        d = datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d")
        ratio_by_day.setdefault(d, []).append(after)

print(f"净敞口拦截 {n} 行；after_pct 分桶：")
for k, v in buckets.most_common():
    print(f"   {v:>7}  {v/max(1,n):>6.1%}  after_pct ∈ {k}%")

print("\n按日中位/最大 after_pct（近 15 天）：")
for d in sorted(ratio_by_day)[-15:]:
    xs = sorted(ratio_by_day[d])
    print(f"   {d}  n={len(xs):<5} 中位={xs[len(xs)//2]:>8.1f}%  P90={xs[int(len(xs)*0.9)]:>9.1f}%  max={xs[-1]:>10.1f}%")

print("\n最近 12 条样本（时间 / after% / 触发标的 / 审计标的）：")
for ts, after, trig, sym in sorted(recent)[-12:]:
    t = datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M")
    print(f"   {t}  after={after:>8.1f}%  触发={trig:<8} 审计标的={sym}")
