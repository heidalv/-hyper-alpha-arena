# -*- coding: utf-8 -*-
"""V13 当前日志门禁分布（流式，避免大文件工具差异）。"""
import re
from collections import Counter

P = r"D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log"
pats = {
    "skip_reason": re.compile(r"MidLongAudit\] skip stage=exec symbol=(\S+) reason=([^\s]+)"),
    "fuse": re.compile(r"stage=fuse .*?reason=([^\s]+)"),
    "location_gate": re.compile(r"location_gate[_a-z]*"),
    "learned_long": re.compile(r"learned_long_[a-z]+"),
    "block_reason": re.compile(r"\[MidLongPortfolio\] BLOCK (\S+) (\S+): ([^\(]+)"),
    "regime_short": re.compile(r"midlong_short_[a-z_]+"),
    "trend_broken_gate": re.compile(r"trend_broken 价格闸"),
}
c = {k: Counter() for k in pats}
tmin = tmax = None
n = 0
with open(P, encoding="utf-8", errors="replace") as f:
    for line in f:
        n += 1
        if n % 50000 == 0:
            pass
        ts = line[:19]
        if ts.startswith("2026-"):
            if tmin is None:
                tmin = ts
            tmax = ts
        for k, p in pats.items():
            m = p.search(line)
            if m:
                if k == "skip_reason":
                    c[k][m.group(2).split(":")[0] if ":" in m.group(2) else m.group(2)] += 1
                elif k == "block_reason":
                    c[k][m.group(3).strip()[:40]] += 1
                else:
                    c[k][m.group(0)] += 1

print(f"总行数 {n}  时间范围 {tmin} → {tmax}")
for k in pats:
    print(f"\n== {k} ==")
    if not c[k]:
        print("  (无)")
    for name, cnt in c[k].most_common(12):
        print(f"  {name:<50} {cnt}")
