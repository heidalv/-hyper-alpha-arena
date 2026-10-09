# -*- coding: utf-8 -*-
"""[F263] 「频率 vs 盈亏」正式实验：
w_base_bp ∈ {6, 8, 10, 12} × 3 延迟 × 3 窗口（全天 / 当日含快跌 / 快盘夜）。
指标：成交笔数（频率）、net_usd、net_bp、dd%、每 100 笔净额（单位频率质量）。
其余参数=在位审计配置；基线=锚定值。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from backend.services import lane_registry as reg
from backend.services.market_maker import evolution as evo
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio

LANE = "mm_asterdex"

def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    data = _load_all(symbols, venue)
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")

    windows = [
        ("全天 09-15（16h）", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00", 16.0),
        ("当日 09-16 08:00→now", "2026-09-16T08:00:00+08:00", now_iso,
         max(0.5, (_ms(now_iso) - _ms("2026-09-16T08:00:00+08:00")) / 3.6e6)),
        ("快盘夜 09-15 22:05→02:00", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00", 3.9),
    ]

    for wname, s0, s1, hours in windows:
        since_ms, until_ms = _ms(s0), _ms(s1)
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

        print(f"\n=== {wname}（时长 {hours:.1f}h）===")
        print(f"{'w_bp':>5s} {'delay':>7s} {'fills':>6s} {'fills/h':>7s} {'net_usd':>9s} "
              f"{'net_bp':>8s} {'/100笔$':>8s} {'dd%':>6s}")
        for w in (6.0, 8.0, 10.0, 12.0):
            qp2 = QuoteParams(**{**{k: getattr(qp, k) for k in QuoteParams.__dataclass_fields__},
                                 "w_base_bp": w})
            for d in evo.ROBUST_DELAYS_MS:
                r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp2, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                     enforce_lane_limits=True, tick_delay_ms=float(d),
                                     fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
                fills = r.get("fills") or 0
                net = r.get("net_usd") or 0.0
                per100 = (net / fills * 100.0) if fills else 0.0
                print(f"{w:>5.1f} {d/1000:>6.1f}s {fills:>6} {fills/hours:>7.1f} {net:>+9.3f} "
                      f"{(r.get('net_bp') or 0):>+8.3f} {per100:>+8.2f} "
                      f"{(r.get('max_dd_pct') or 0):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
