# -*- coding: utf-8 -*-
"""[F250 研究] 波动闸阈值敏感性：坏夜晚(09-15 22:05-02:00) vs 平静早盘(09-15 08:00-12:00)。

同窗口同配置，只扫 vol_pause_sigma（0=关 / 0.7=在位 / 0.5 / 0.3 / 0.1），
delay 固定 25.1s（最优平台中位），其余参数一律取在位配置。
输出：各阈值下 成交数 / 净收益 / maker bp / 平仓 bp / 最大回撤%。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from backend.services import lane_registry as reg
from backend.services.market_maker import evolution as evo
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


def run_window(name, since_txt, until_txt, lane, sweep):
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    sub, seed = window(symbols, venue, since_txt, until_txt)
    print(f"\n=== {name}（{since_txt} ~ {until_txt}）===")
    print(f"{'vol_pause_sigma':>16s} {'fills':>6s} {'net_usd':>9s} {'net_bp':>8s} "
          f"{'maker_bp':>9s} {'flat_bp':>8s} {'dd%':>6s}")
    for vps in sweep:
        lim2 = LaneRiskLimits(**{**{k: getattr(lim, k) for k in LaneRiskLimits.__dataclass_fields__},
                                 "vol_pause_sigma": vps})
        r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim2,
                             fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                             enforce_lane_limits=True, tick_delay_ms=25100.0,
                             fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
        print(f"{vps:>16.1f} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} {r.get('net_bp'):>+8.3f} "
              f"{r.get('maker_net_bp'):>+9.3f} {r.get('flatten_net_bp'):>+8.3f} {r.get('max_dd_pct'):>6.2f}")


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    sweep = [0.7, 0.5, 0.3, 0.1, 0.0]
    run_window("坏夜晚（重置后 22:05 → 02:00）",
               "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00", lane, sweep)
    run_window("平静早盘对照（09-15 08:00 → 12:00）",
               "2026-09-15T08:00:00+08:00", "2026-09-15T12:00:00+08:00", lane, sweep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
