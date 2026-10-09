# -*- coding: utf-8 -*-
"""[F241b] 逐日 × 分腿组件分解（最终配置，25.1s）：
被动腿的价差捕获(spread) vs 价格漂移(price) vs 费(fee)，逐日对比——
定位"薄边际日"的亏损机制：是价差捕获少了，还是逆选择（price）大了。
"""
import sys
from datetime import datetime
from collections import defaultdict

import numpy as np

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import (  # noqa: E402
    _load_all,
    replay_portfolio,
)

DAYS = ["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15"]

lane = reg.get_lane("mm_asterdex") or {}
meta = dict(lane.get("meta") or {})
symbols = list(meta.get("symbols") or [])
venue = str(meta.get("venue") or "asterdex")
cur = dict(meta.get("params") or {})
qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
data = _load_all(symbols, venue)

print(f"{'日':<12}{'被动腿':>5}{'spread bp':>10}{'price bp':>10}{'net bp':>9}"
      f"{'平仓腿':>5}{'fl_net_bp':>10}")
for day in DAYS:
    since_ms = int(datetime.fromisoformat(day + "T08:00:00+08:00").timestamp() * 1000)
    until_ms = int(datetime.fromisoformat(day + "T23:59:00+08:00").timestamp() * 1000)
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
    mk = defaultdict(float)
    for x in r.get("fills_log") or []:
        if x.get("flatten"):
            continue
        mk["ntl"] += float(x["notional"])
        mk["spread"] += float(x.get("spread_usd") or 0.0)
        mk["price"] += float(x.get("price_usd") or 0.0)
    sp = mk["spread"] / mk["ntl"] * 1e4 if mk["ntl"] else 0.0
    pr = mk["price"] / mk["ntl"] * 1e4 if mk["ntl"] else 0.0
    nt = (mk["spread"] + mk["price"]) / mk["ntl"] * 1e4 if mk["ntl"] else 0.0
    print(f"{day:<12}{len([x for x in r.get('fills_log') or [] if not x.get('flatten')]):>5}"
          f"{sp:>+10.3f}{pr:>+10.3f}{nt:>+9.3f}"
          f"{r.get('flattens'):>5}{float(r.get('flatten_net_bp') or 0):>+10.3f}")
