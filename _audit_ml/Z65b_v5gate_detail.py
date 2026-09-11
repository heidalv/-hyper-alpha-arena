# -*- coding: utf-8 -*-
"""Z65b: 1866 条通用拒仓成因定位——[V5Gate] BLOCK detail 分布（09-08~09-10）。"""
from __future__ import annotations
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "logs" / "backend-console.log"
rx = re.compile(r"\[V5Gate\] BLOCK symbol=(\S+) tier=(\S+) action=(\S+) detail=(.*)$")
c_detail, c_tier, c_sym, n = Counter(), Counter(), Counter(), 0
samples = {}
with p.open(encoding="utf-8", errors="ignore") as f:
    for line in f:
        m = rx.search(line)
        if not m:
            continue
        n += 1
        sym, tier, act, detail = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        key = detail.split("(")[0].split(":")[0].strip()[:70] or detail[:70]
        c_detail[key] += 1
        c_tier[tier] += 1
        c_sym[sym] += 1
        samples.setdefault(key, detail[:150])
print("总 [V5Gate] BLOCK 行数:", n)
print("\n按 detail 前缀:")
for k, v in c_detail.most_common(25):
    print(f"  {v:>6}  {k}")
    print(f"          样本: {samples[k]}")
print("\n按 tier:", dict(c_tier))
print("按 symbol:", dict(c_sym.most_common(10)))
