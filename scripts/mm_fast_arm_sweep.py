# -*- coding: utf-8 -*-
"""[F264 扫描] stop_loss_fast_mult ∈ {0,3,5,8} × 3 延迟 × 3 窗口
（全天 09-15 / 当日含快跌 / 快盘夜）。其余参数=在位配置。
问题：快武装能否在快跌/快盘窗口把止损提前（减少平仓漂移），且平静窗口不劣？
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
        ("全天 09-15", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
        ("当日 09-16 08:00→now", "2026-09-16T08:00:00+08:00", now_iso),
        ("快盘夜 22:05→02:00", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00"),
    ]
    for wname, s0, s1 in windows:
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

        print(f"\n=== {wname} ===")
        print(f"{'fm':>5s} {'delay':>7s} {'fills':>6s} {'net_usd':>9s} {'net_bp':>8s} "
              f"{'flat_bp':>8s} {'flattens':>8s} {'dd%':>6s}")
        for fm in (0.0, 3.0, 5.0, 8.0):
            lim2 = LaneRiskLimits(**{**{k: getattr(lim, k) for k in LaneRiskLimits.__dataclass_fields__},
                                     "stop_loss_fast_mult": fm})
            for d in evo.ROBUST_DELAYS_MS:
                r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim2,
                                     fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                     enforce_lane_limits=True, tick_delay_ms=float(d),
                                     fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
                print(f"{fm:>5.1f} {d/1000:>6.1f}s {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                      f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('flatten_net_bp') or 0):>+8.3f} "
                      f"{r.get('flattens'):>8} {(r.get('max_dd_pct') or 0):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
