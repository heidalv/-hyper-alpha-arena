# -*- coding: utf-8 -*-
"""Z178（P11 定量）：`chart_gate_veto` 的实际年龄分布与 `position_advice` 构成。

问题（§59.1）：图审拦截占漏斗 27%，其中 ~98% 是单条 `position_advice=no_new_long`。
图审 8h 一轮，而否决有效期是"信号年龄 ≤ MIDLONG_CHART_MAX_SIGNAL_AGE_MIN（默认 240min）"
⇒ 若图审给出 no_new_long，它在接下来 4h 内持续否决买入（占每轮 50% 时间）。

本脚本量化：近 N 小时 chart_gate_veto 的条数、其中 no_new_long 占比、
以及（若审计写入了 detail）信号年龄分布，为"是否给 position_advice 单独设更短 TTL"提供依据。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.services.mlto.midlong_direction_audit import audit_paths, _iter_rows  # noqa: E402

WINDOW_H = float(sys.argv[1]) if len(sys.argv) > 1 else 48.0
since = time.time() - WINDOW_H * 3600

reasons: Counter[str] = Counter()
advices: Counter[str] = Counter()
ages: list[int] = []
rows_n = 0
scanned = 0
old = 0
for r in _iter_rows(audit_paths()):  # 注意：_iter_rows 收的是**路径列表**（传字符串会逐字符失败）
    scanned += 1
    if float(r.get("epoch") or 0) < since:
        old += 1
        continue
    reason = str(r.get("reason") or "")
    if "chart_gate" not in reason:
        continue
    rows_n += 1
    reasons[reason[:90]] += 1
    detail = r.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:  # noqa: BLE001
            detail = {}
    if isinstance(detail, dict):
        if detail.get("position_advice"):
            advices[str(detail["position_advice"])] += 1
        try:
            ages.append(int(detail.get("signal_age_min")))
        except (TypeError, ValueError):
            pass

print(f"窗口 {WINDOW_H:.0f}h：chart_gate 相关审计行 {rows_n}")
print(f"（诊断：扫描 {scanned} 行，其中 {old} 行早于窗口；since={since:.0f}）")
print("\n=== reason 分布 TOP10 ===")
for k, v in reasons.most_common(10):
    print(f"  {v:6d}  {k}")
print("\n=== position_advice 分布 ===")
for k, v in advices.most_common():
    print(f"  {v:6d}  {k}")
if ages:
    ages.sort()
    n = len(ages)
    def q(p):
        return ages[min(n - 1, int(n * p))]
    print(f"\n=== signal_age_min 分布（n={n}）===")
    print(f"  min={ages[0]}  p25={q(.25)}  p50={q(.5)}  p75={q(.75)}  p90={q(.9)}  max={ages[-1]}")
    buckets = Counter()
    for a in ages:
        buckets["0-30" if a <= 30 else "31-60" if a <= 60 else "61-120" if a <= 120
                else "121-240" if a <= 240 else ">240"] += 1
    for k in ("0-30", "31-60", "61-120", "121-240", ">240"):
        if buckets.get(k):
            print(f"    {k:8s} {buckets[k]:6d}")
else:
    print("\n（审计行未携带 signal_age_min —— 需要给 chart_gate 的 detail 增加该字段才能量化；见 §69）")
