# -*- coding: utf-8 -*-
"""Z65c: [V5Gate] BLOCK 行的真实格式分布（找出 2098 条非 detail 格式的来源）。"""
from __future__ import annotations
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "logs" / "backend-console.log"
shapes = Counter()
samples = {}
with p.open(encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "[V5Gate] BLOCK" not in line:
            continue
        i = line.index("[V5Gate] BLOCK")
        body = line[i:].strip()
        # 归一化：去掉 symbol/tier/action 的具体值
        shape = re.sub(r"symbol=\S+", "symbol=*", body)
        shape = re.sub(r"tier=\S+", "tier=*", shape)
        shape = re.sub(r"action=\S+", "action=*", shape)
        shape = re.sub(r"=\S+", "=*", shape)
        shapes[shape[:110]] += 1
        samples.setdefault(shape[:110], body[:220])
print("形状分布:")
for k, v in shapes.most_common(20):
    print(f"  {v:>6}  {k}")
    print(f"          {samples[k]}")
print("\n前 6 条原始行（不限格式）:")
n = 0
with p.open(encoding="utf-8", errors="ignore") as f:
    for line in f:
        if "[V5Gate] BLOCK" in line:
            print("   ", line.strip()[:230])
            n += 1
            if n >= 6:
                break
