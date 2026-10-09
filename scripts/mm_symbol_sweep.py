# -*- coding: utf-8 -*-
"""[F259] 币种选择扫描：BTC-only / ETH-only / BTC+ETH / 全 5 币 在
09-15 全天 + 09-16 当日部分窗口（在位配置 + 锚定基线，delay=31.8s）。
动机：逐币数据里 BTC 一直最优（09-15 时代 −0.20 vs SOL −13.82；今日 +0.03 为正）。
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


def _win(symbols, venue, since_txt, until_txt):
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
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    combos = {
        "BTC-only": ["BTC"], "ETH-only": ["ETH"], "BTC+ETH": ["BTC", "ETH"],
        "全5币": ["BTC", "ETH", "BNB", "XRP", "SOL"],
    }
    for wname, s0, s1 in (
        ("09-15 全天", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
        ("09-16 当日(08:00→now)", "2026-09-16T08:00:00+08:00", now_iso),
    ):
        print(f"\n=== {wname}（delay 31.8s）===")
        print(f"{'组合':>10s} {'fills':>6s} {'net_usd':>9s} {'net_bp':>8s} "
              f"{'maker_bp':>9s} {'flat_bp':>8s} {'dd%':>6s}")
        for name, syms in combos.items():
            sub, seed = _win(syms, venue, s0, s1)
            r = replay_portfolio(syms, venue=venue, equity=300.0, params=qp, limits=lim,
                                 fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=31800.0,
                                 fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            print(f"{name:>10s} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                  f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+9.3f} "
                  f"{(r.get('flatten_net_bp') or 0):>+8.3f} {(r.get('max_dd_pct') or 0):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
