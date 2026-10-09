# -*- coding: utf-8 -*-
"""[F256 研究] 两条关键测量：
  A. 时代窗口模型重放（09-15 22:05 → now，3 延迟）——量化 live↔model 差距（F250#3 收尾）；
  B. BTC+BNB 子集（去掉 ETH/XRP/SOL）在 09-15 全天 —— 子集证据（F238 提示 +0.66/6d）。
配置 = 在位审计配置；基线 = 注册表锚定值。
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


def run(name, symbols, since_txt, until_txt, lane, vb, qp, lim):
    venue = str((lane.get("meta") or {}).get("venue") or "asterdex")
    sub, seed = _win(symbols, venue, since_txt, until_txt)
    print(f"\n=== {name}（{len(symbols)} 币）===")
    print(f"{'delay':>7s} {'fills':>6s} {'net_usd':>9s} {'net_bp':>8s} "
          f"{'maker_bp':>9s} {'flat_bp':>8s} {'flattens':>8s} {'dd%':>6s}")
    for d in evo.ROBUST_DELAYS_MS:
        r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                             fill_notional=300.0, data=sub, vol_baseline=vb,
                             enforce_lane_limits=True, tick_delay_ms=float(d),
                             fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
        print(f"{d/1000:>6.1f}s {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
              f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+9.3f} "
              f"{(r.get('flatten_net_bp') or 0):>+8.3f} {r.get('flattens'):>8} "
              f"{(r.get('max_dd_pct') or 0):>6.2f}")


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    run("A. 时代窗口（09-15 22:05 → now，模型）", symbols,
        "2026-09-15T22:05:00+08:00", now_iso, lane, vb, qp, lim)
    run("B. BTC+BNB 子集 · 09-15 全天", ["BTC", "BNB"],
        "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00", lane, vb, qp, lim)
    run("B2. 全 5 币 · 09-15 全天（对照）", symbols,
        "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00", lane, vb, qp, lim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
