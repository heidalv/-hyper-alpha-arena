# -*- coding: utf-8 -*-
"""Z110: `_bump_live_open_quota()` 的 13 个调用点是"开仓"还是"平仓/减仓"？

配额语义：`live_daily_open_bump()` 把 per-session 按日的 used +1；开仓前
`live_enforce_daily_open_cap()` 用它拦单。**若在平仓/减仓路径也 +1，则"关得越多、开得越少"**。
判定：看每个调用点前后 12 行内的动作关键词（close/reduce/take_profit/stop_loss vs 开仓关键词）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "backend/services/full_auto/master_execution.py"
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()

CLOSE_RX = re.compile(
    r"(close_position|partial_close|reduce|take_profit|stop_loss|ai_cut_loss|ai_take_profit|"
    r"hard_line_close|trailing|flatten|平仓|减仓|止盈|止损)", re.I)
OPEN_RX = re.compile(r"(place_order|open|buy|sell|开仓|建仓|pyramid|dca)", re.I)

sites = [i for i, l in enumerate(lines) if "_bump_live_open_quota()" in l and "def " not in l]
print(f"调用点 {len(sites)} 个\n")
verdict = {"CLOSE-ish": [], "OPEN-ish": [], "UNCLEAR": []}
for i in sites:
    lo, hi = max(0, i - 18), min(len(lines), i + 6)
    seg = "\n".join(lines[lo:hi])
    n_close = len(CLOSE_RX.findall(seg))
    n_open = len(OPEN_RX.findall(seg))
    kind = "CLOSE-ish" if n_close > n_open else ("OPEN-ish" if n_open > n_close else "UNCLEAR")
    verdict[kind].append(i + 1)
    print(f"=== master_execution.py:{i+1}  [{kind}]  close词={n_close} open词={n_open} ===")
    for j in range(max(0, i - 6), min(len(lines), i + 3)):
        mark = ">>" if j == i else "  "
        s = lines[j].strip()
        if s and not s.startswith("#"):
            print(f"   {mark} {s[:150]}")

print("\n=== 汇总 ===")
for k, v in verdict.items():
    print(f"  {k}: {len(v)}  {v}")
