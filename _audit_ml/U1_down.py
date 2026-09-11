# -*- coding: utf-8 -*-
"""down-regime 剩余失血源诊断（第十三轮）。"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.scripts.replay_midlong_policy import (  # noqa: E402
    cost_pct,
    daily_series,
    load_klines,
    load_trades,
    pick,
    regime_at,
    sim_exit,
)

d = json.load(open(ROOT / "data" / "midlong_policy_replay.json", encoding="utf-8"))
down = [r for r in d["rows"] if r["regime"] == "down"]
print(f"down regime 通过闸门的交易: {len(down)}")
for r in down:
    print(f"  {r['symbol']} {r['side']} 毛={r['gross_pct']:+.2f}% 净={r['net_pct']:+.2f}% "
          f"持有={r['hold_h']}h 出场={r['exit']}")

print("\n=== 反事实：down regime 空仓 vs 只做多 ===")
rows = d["rows"]
for name, filt in [
    ("down 保留（当前）", lambda r: True),
    ("down 空仓", lambda r: r["regime"] != "down"),
    ("down 只做多（即使 regime 门禁多）", lambda r: r["regime"] != "down"),
]:
    rr = [r for r in rows if filt(r)]
    nets = [r["net_pct"] for r in rr]
    print(f"  {name:<22} n={len(rr)} 净均值={sum(nets)/len(nets):+.3f}% 胜率={sum(1 for x in nets if x>0)/len(nets):.3f}")

# 用更长持有期 / 更紧 SL 试 down 空头（原始数据重算）
trades = load_trades()
data, daily = load_klines({t["symbol"] for t in trades})
print("\n=== down 空头参数重试（原始信号） ===")
for sl, trig, gap, mh in [(6, 3, 1.5, 168), (4, 2, 1.5, 168), (8, 4, 3, 336), (6, 3, 2, 336)]:
    rr = []
    for t in trades:
        if t["side"] != "short":
            continue
        s = pick(data, t["symbol"])
        if not s:
            continue
        ts = int(t["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None:
            continue
        ds = daily_series(daily, t["symbol"])
        if regime_at(ds, ts) != "down":
            continue
        g, hold, kind = sim_exit(s, i, "short", sl_pct=sl, trig=trig, gap=gap, max_h=mh)
        rr.append(g - cost_pct(hold))
    if rr:
        print(f"  SL{sl}/trig{trig}/gap{gap}/max{mh}h: n={len(rr)} 净均值={sum(rr)/len(rr):+.3f}% "
              f"胜率={sum(1 for x in rr if x>0)/len(rr):.3f}")
