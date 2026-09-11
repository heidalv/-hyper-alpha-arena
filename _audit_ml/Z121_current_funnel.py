# -*- coding: utf-8 -*-
"""Z121: 时间切片后的**当前**漏斗（近 5 天 vs 历史窗口）——订正 §57.2 的"最大瓶颈"结论。"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.mlto.midlong_direction_audit import _iter_rows  # noqa: E402


def funnel(since_hours: float, until_hours: float = 0.0):
    now = time.time()
    lo = now - since_hours * 3600
    hi = now - until_hours * 3600
    oc, reasons, stages, syms = Counter(), Counter(), Counter(), Counter()
    n = 0
    for row in _iter_rows():
        ts = float(row.get("epoch") or 0)
        if not (lo <= ts <= hi):
            continue
        n += 1
        oc[str(row.get("outcome") or "?")] += 1
        if str(row.get("outcome")) == "skip":
            r = str(row.get("reason") or "?")
            reasons[r.split("(")[0].split(":")[0][:52]] += 1
            stages[str(row.get("stage") or "?")] += 1
            syms[str(row.get("symbol") or "?")] += 1
    return n, oc, reasons, stages, syms


for label, hrs, until in (("近 5 天", 24 * 5, 0.0),
                          ("08-17~09-05（v2 启用期）", 24 * 20, 24 * 5)):
    n, oc, reasons, stages, syms = funnel(hrs, until)
    print(f"\n{'='*90}\n=== {label}: 行数 {n} ===")
    print("  outcome:", dict(oc))
    print("  stage:", dict(stages))
    tot = sum(reasons.values())
    print(f"  top skip reasons（共 {tot}）:")
    for k, v in reasons.most_common(10):
        print(f"     {v:>7}  {v/max(1,tot):>6.1%}  {k}")
    print("  top symbols:", dict(syms.most_common(8)))
