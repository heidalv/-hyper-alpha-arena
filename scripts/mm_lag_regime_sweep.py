# -*- coding: utf-8 -*-
"""[F251 研究] 延迟 × 快盘交互：坏窗口（09-15 22:05–02:00）与平静早盘（08:00–12:00）
× judge 延迟 {12, 18.2, 25.1, 31.8, 45.0}s（45s≈实盘 3 桶）。同窗口同配置。
假说：快盘里短延迟更优（F250: 实盘 45s 旧单被深度穿透），平静盘长延迟更优（F245）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from backend.services import lane_registry as reg
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio

LANE = "mm_asterdex"

def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def window(symbols, venue, since_txt, until_txt):
    data = _load_all(symbols, venue)
    since_ms, until_ms = _ms(since_txt), _ms(until_txt)
    sub, seed = {}, {}
    for s in symbols:
        dd = data[s]
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
        t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
        t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
        sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba")}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = dd[k][t2:t3]
        lo_i = max(0, a2 - 240)
        seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                   for k in range(lo_i, a2)) if x > 0]
    return sub, seed


def run_window(name, since_txt, until_txt, lane):
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    sub, seed = window(symbols, venue, since_txt, until_txt)
    print(f"\n=== {name} ===")
    print(f"{'delay':>7s} {'fills':>6s} {'net_usd':>9s} {'net_bp':>8s} "
          f"{'maker_bp':>9s} {'flat_bp':>8s} {'dd%':>6s}")
    for d in (12000.0, 18200.0, 25100.0, 31800.0, 45000.0):
        r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                             fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                             enforce_lane_limits=True, tick_delay_ms=d,
                             fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
        print(f"{d/1000:>6.1f}s {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
              f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+9.3f} "
              f"{(r.get('flatten_net_bp') or 0):>+8.3f} {(r.get('max_dd_pct') or 0):>6.2f}")


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    run_window("坏窗口（09-15 22:05–02:00，快盘）",
               "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00", lane)
    run_window("平静早盘（09-15 08:00–12:00）",
               "2026-09-15T08:00:00+08:00", "2026-09-15T12:00:00+08:00", lane)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
