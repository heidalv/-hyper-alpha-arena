# -*- coding: utf-8 -*-
"""[F258] 09-16 当日部分窗口（08:00 → now）模型三延迟 vs 实盘同窗口。
供 23:59 全天 digest 的先导读数：模型今天亏多少、实盘亏多少、差距多大。
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
from backend.services import lane_ledger

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

    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    since_txt = "2026-09-16T08:00:00+08:00"

    data = _load_all(symbols, venue)
    since_ms, until_ms = _ms(since_txt), _ms(now_iso)
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

    print(f"=== 09-16 08:00 → now（{now_iso}）模型三延迟 ===")
    for d in evo.ROBUST_DELAYS_MS:
        r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                             fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                             enforce_lane_limits=True, tick_delay_ms=float(d),
                             fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
        print(f"  {d/1000:>6.1f}s fills={r.get('fills'):>4} net={r.get('net_usd'):>+8.3f} "
              f"net_bp={(r.get('net_bp') or 0):>+7.3f} maker={(r.get('maker_net_bp') or 0):>+7.2f} "
              f"flat={(r.get('flatten_net_bp') or 0):>+7.2f} dd={(r.get('max_dd_pct') or 0):.2f}%")

    # 实盘同窗口（账本，排除校正行）
    attr = lane_ledger.attribution(days=1, lane_id=LANE, since=since_txt)
    t = attr.get("total") or {}
    print(f"实盘同窗口: n={t.get('n')} net_usd={t.get('net_usd')} net_bp={t.get('net_bp')} "
          f"spread_bp={t.get('spread_bp')} price_bp={t.get('price_bp')}")
    by = attr.get("by_symbol") or []
    for x in sorted(by, key=lambda r: r.get("net_usd") or 0):
        print(f"    {x['symbol']}: n={x['n']} net={x['net_usd']:+8.3f} "
              f"spread={x['spread_bp']:+7.2f} price={x['price_bp']:+7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
