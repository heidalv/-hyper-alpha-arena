# -*- coding: utf-8 -*-
"""[F223] 校验「当前 regime vs 波动基准」：同一数据源、同一函数，回答两问——
   ① 当前 σ 在 14 天分布里处于什么分位（是不是真的异常高）？
   ② 把基准重锚到 14 天中位后，`vol_pause_sigma=1.0` 还拦不拦？
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import realized_vol_bp  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all  # noqa: E402

LANE = sys.argv[1] if len(sys.argv) > 1 else "mm_asterdex"
W = 20

lane = reg.get_lane(LANE) or {}
meta = lane.get("meta") or {}
symbols = list(meta.get("symbols") or [])
venue = str(meta.get("venue") or "asterdex")
old = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

data = _load_all(symbols, venue)
print(f"{'币':<5}{'样本数':>9}{'旧基准':>10}{'14d中位':>10}{'当前σ':>10}"
      f"{'σ/旧':>8}{'σ/新':>8}{'当前分位':>10}")
for s in symbols:
    d = data[s]
    ms = [float((d["bb"][i] + d["ba"][i]) / 2) for i in range(len(d["bb"]))]
    ms = [m for m in ms if m > 0]
    vals = []
    if len(ms) >= W + 2:
        vals = [v for v in (realized_vol_bp(ms[j - W:j + 1], W)
                            for j in range(W, len(ms))) if v > 0]
    med = float(np.median(vals)) if vals else 0.0
    cur = float(realized_vol_bp(ms[-(W + 1):], W)) if len(ms) > W else 0.0
    o = float(old.get(s) or 0.0)
    pct = 100.0 * float(np.mean([v <= cur for v in vals])) if vals else 0.0
    print(f"{s:<5}{len(ms):>9}{o:>10.4f}{med:>10.4f}{cur:>10.4f}"
          f"{(cur / o if o else 0):>8.2f}{(cur / med if med else 0):>8.2f}{pct:>9.1f}%")
print("\n判据：`vol_pause_sigma=1.0` 要求 σ/基准 ≤ 1.0 才允许报价。")
print("若『σ/新』列仍 > 1.0 ⇒ **重锚基准并不能解除封停**（只是把基准换成 14 天中位）。")
