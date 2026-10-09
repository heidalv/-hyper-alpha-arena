# -*- coding: utf-8 -*-
"""[F234] 币种子集回放：把被动边≤0 的币（SOL/ETH）砍掉后，总净额变多少？
只读，不落地。与在位配置同窗口同口径（25.1s）。
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import (  # noqa: E402
    _load_all,
    replay_portfolio,
)

SUBSETS = {
    "全5币": None,
    "BTC+BNB+XRP": ["BTC", "BNB", "XRP"],
    "BTC+BNB": ["BTC", "BNB"],
}
WBASES = [8.0]
GATES = {"side_trend_min_bp": 10.0, "trend_pause_bp": 15.0}
# [F238] 多日回放：每段一个自然日 08:00→23:59（09-10~09-14 为 F171 前旧桶数据，
# 成交率约一半，仅作方向参考；09-15 为干净数据日 ✓）
DAYS = [
    ("2026-09-10T08:00:00+08:00", "2026-09-10T23:59:00+08:00"),
    ("2026-09-11T08:00:00+08:00", "2026-09-11T23:59:00+08:00"),
    ("2026-09-12T08:00:00+08:00", "2026-09-12T23:59:00+08:00"),
    ("2026-09-13T08:00:00+08:00", "2026-09-13T23:59:00+08:00"),
    ("2026-09-14T08:00:00+08:00", "2026-09-14T23:59:00+08:00"),
    ("2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
]

lane = reg.get_lane("mm_asterdex") or {}
meta = dict(lane.get("meta") or {})
all_symbols = list(meta.get("symbols") or [])
venue = str(meta.get("venue") or "asterdex")
cur = dict(meta.get("params") or {})
vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
data = _load_all(all_symbols, venue)

for day_since, day_until in DAYS:
    since_ms = int(datetime.fromisoformat(day_since).timestamp() * 1000)
    until_ms = int(datetime.fromisoformat(day_until).timestamp() * 1000)
    for wbase in WBASES:
        cur2 = dict(cur)
        cur2["w_base_bp"] = wbase
        cur2.update(GATES)
        qp = QuoteParams(**{k: v for k, v in cur2.items() if k in QuoteParams.__dataclass_fields__})
        lim = LaneRiskLimits(**{k: v for k, v in cur2.items() if k in LaneRiskLimits.__dataclass_fields__})
        for name, symbols in SUBSETS.items():
            symbols = symbols or all_symbols
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
            r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                                 fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=25100.0,
                                 fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            print(f"{day_since[5:10]} w={wbase:<3} {name:<12} fills={r.get('fills'):>4}  "
                  f"net_usd={r.get('net_usd'):>+8.3f}  net_bp={r.get('net_bp'):>+8.3f}"
                  f"  maker={r.get('maker_net_bp')}  flatten={r.get('flatten_net_bp')}"
                  f"  flattens={r.get('flattens')}  dd={r.get('max_dd_pct')}%")
