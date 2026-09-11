# -*- coding: utf-8 -*-
"""Z88: 563 条 EV 闸记录的 tp/sl 形态 → 判定它们来自 mid 层还是 long 层（E1 vs E2）。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RX = re.compile(
    r"\[MidLongEvGate\] (\S+) \[影子·未校准放行\] EV=([-\d.]+)%.*?tp=([\d.]+)%×([\d.]+) sl=([\d.]+)%×([\d.]+).*?\[(\w+)\]"
)
shape = Counter()
samples = {}
syms = Counter()
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
seen = set()
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            if "[MidLongEvGate]" not in line or "[影子·未校准放行]" not in line:
                continue
            key = line.strip()[:200]
            if key in seen:
                continue
            seen.add(key)
            m = RX.search(line)
            if not m:
                continue
            sym, ev, tp, tpr, sl, slr, nat = m.groups()
            k = f"tp={tp}% sl={sl}% nature={nat}"
            shape[k] += 1
            syms[sym] += 1
            samples.setdefault(k, line.strip()[:200])
print("形态分布（tp/sl 可区分 mid[≈5%/4.5%] 与 long[≈10%/6.5%]）:")
for k, v in shape.most_common(12):
    print(f"  {v:>5}  {k}")
    print(f"         样本: {samples[k][:190]}")
print("\n按标的:", dict(syms.most_common(12)))
