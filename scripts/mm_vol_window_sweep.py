# -*- coding: utf-8 -*-
"""[F252 研究] 止损武装的 vol 窗口敏感性：
XRP 快跌（09-31→09:35，−75bp）里，σ_norm 用 20 样本（≈10 分钟）窗口 ⇒ 武装太晚，
止损成交在 −75bp（触发线 10bp）。扫 vol_window ∈ {20,10,5}：
  · 崩溃窗口（09:30–10:00）看止损是否更早、净损是否更小；
  · 全天（09-15）看短窗口是否全口径不劣（短窗口会让 vol_pause 更敏感、多停）。
delay 固定 31.8s（全日最优延迟）。
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


def _window(symbols, venue, since_txt, until_txt):
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


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    for name, s0, s1 in (
        ("崩溃窗口 09:30–10:00（09-15）", "2026-09-15T09:30:00+08:00", "2026-09-15T10:00:00+08:00"),
        ("全天 09-15（08:00–23:59）", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
    ):
        sub, seed = _window(symbols, venue, s0, s1)
        print(f"\n=== {name} ===  (delay 31.8s)")
        print(f"{'vol_win':>8s} {'fills':>6s} {'net_usd':>9s} {'net_bp':>8s} "
              f"{'maker_bp':>9s} {'flat_bp':>8s} {'flattens':>8s} {'dd%':>6s}")
        for vw in (20, 10, 5):
            lim2 = LaneRiskLimits(**{**{k: getattr(lim, k) for k in LaneRiskLimits.__dataclass_fields__},
                                     "vol_window": vw})
            r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim2,
                                 fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=31800.0,
                                 fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            print(f"{vw:>8d} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                  f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+9.3f} "
                  f"{(r.get('flatten_net_bp') or 0):>+8.3f} {r.get('flattens'):>8} "
                  f"{(r.get('max_dd_pct') or 0):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
