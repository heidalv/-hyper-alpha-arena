# -*- coding: utf-8 -*-
"""V12 通道熔断现状：fusion_attribution.json 里各 tier×通道的滚动窗口胜率，
以及「若开启 EXIT_CHANNEL_REBUILD_ON_LOAD=true 会被 shadow 的通道」。"""
import json
import os
from collections import defaultdict

P = r"D:\001Alpha\Hyper-Alpha-Arena\data\fusion_attribution.json"
with open(P, encoding="utf-8") as f:
    d = json.load(f)

breaker = d.get("breaker", {}) or {}
print(f"state ts={d.get('ts')} 通道数={len(breaker)} 磁盘 breaker_shadow={d.get('breaker_shadow')}")

WIN = int(os.getenv("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30)
MAXWR = float(os.getenv("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
ROLL = 30
win_n = min(WIN, ROLL)

rows = []
for bkey, st in breaker.items():
    rec = st.get("recent") if isinstance(st, dict) else None
    if not isinstance(rec, list):
        continue
    n = len(rec)
    wr_all = sum(rec) / n if n else 0
    last = rec[-win_n:]
    wr_win = sum(last) / len(last) if last else 0
    shadow = (len(last) >= win_n) and (wr_win < MAXWR)
    rows.append((bkey, n, wr_all, len(last), wr_win, shadow))

rows.sort(key=lambda x: x[4])
print(f"\n阈值: 窗口 n≥{win_n} 且 胜率<{MAXWR:.0%} → shadow\n")
print(f"{'通道(tier|reason)':<34} {'累计n':>6} {'累计胜率':>8} {'窗内n':>6} {'窗内胜率':>8} {'重建后shadow':>12}")
for bkey, n, wa, wn, ww, sh in rows:
    print(f"{bkey:<34} {n:>6} {wa*100:>7.1f}% {wn:>6} {ww*100:>7.1f}% {'YES' if sh else '-':>12}")

print(f"\n会被 shadow 的通道: {sum(1 for r in rows if r[5])}/{len(rows)}")
