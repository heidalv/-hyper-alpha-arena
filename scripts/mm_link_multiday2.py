# -*- coding: utf-8 -*-
"""[F299 2026-09-16] LINK multi-day recheck with the ANCHORED vol baseline (ASCII-only file).

Why: F296 anchored LINK's vol_baseline_bp = 3.2984 into the lane registry meta. The earlier
F294/F295 evidence used a window-computed baseline ("vol_baseline=None"), i.e. a slightly
different basis than what the live lane now runs. This script re-runs the pre-registered
7-day criterion WITH the anchored baseline so the evidence matches the deployed config.

Pre-registered criterion (unchanged): positive days >= 5/7, total net > 0, mean markout90 >= 0.
Usage: F295_WIDTHS=30 F295_DELAYS=31800 python scripts/mm_link_multiday2.py
"""
from __future__ import annotations

import dataclasses
import io
import os
import sys
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = "mm_asterdex"
SYM = os.getenv("F299_SYM", "LINK")
CST = "+08:00"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _markout(fills, ots, mids, lag=6):
    vals = []
    for f in fills or []:
        try:
            px = float(f.get("px") or 0.0)
            t = float(f.get("ts") or 0.0) * 1000.0
            if px <= 0:
                continue
            i = int(np.searchsorted(ots, t, "left")) + lag
            if i >= len(mids):
                continue
            m = float(mids[i])
            if m <= 0:
                continue
            vals.append((m - px) / px * 1e4 if str(f.get("side")) == "buy"
                        else (px - m) / px * 1e4)
        except Exception:
            continue
    return float(np.mean(vals)) if vals else float("nan")


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp0 = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb_all = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    vb_use = {SYM: float(vb_all[SYM])} if SYM in vb_all else None
    dd = _load_all([SYM], venue).get(SYM)
    if not dd:
        print("no data for", SYM)
        return 1
    widths = tuple(float(x) for x in (os.getenv("F295_WIDTHS", "30")).split(",") if x)
    delays = tuple(float(x) for x in (os.getenv("F295_DELAYS", "31800")).split(",") if x)
    days = [(datetime(2026, 9, 10) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    print(f"[F299] sym={SYM} w={widths} delays={[d/1000 for d in delays]}s "
          f"vol_baseline={vb_use if vb_use else 'window-computed'} "
          f"live_params: w_base_bp={qp0.w_base_bp} vol_pause_sigma={lim.vol_pause_sigma}")
    print(f"{'day':<12}{'snaps':>7}{'w':>5}{'delay':>7}{'fills':>6}{'net$':>9}{'net_bp':>8}"
          f"{'maker':>7}{'flat':>8}{'dd%':>6}{'mk90':>8}")
    tot = {(w, d): 0.0 for w in widths for d in delays}
    pos = {(w, d): 0 for w in widths for d in delays}
    n_day = {(w, d): 0 for w in widths for d in delays}
    mk_all = {(w, d): [] for w in widths for d in delays}
    for day in days:
        since_ms = _ms(f"{day}T08:00:00{CST}")
        until_ms = _ms(f"{day}T23:59:00{CST}")
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
        t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
        t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
        if a3 - a2 < 200:
            print(f"{day:<12}{a3-a2:>7}  insufficient snaps, skipped")
            continue
        sub = {SYM: {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[SYM][k] = dd[k][t2:t3]
        mids = (dd["bb"][a2:a3] + dd["ba"][a2:a3]) / 2.0
        lo_i = max(0, a2 - 240)
        seed = {SYM: [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                                  for k in range(lo_i, a2)) if x > 0]}
        for w in widths:
            for dl in delays:
                qp = dataclasses.replace(qp0, w_base_bp=float(w))
                r = replay_portfolio([SYM], venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=vb_use,
                                     enforce_lane_limits=True, tick_delay_ms=float(dl),
                                     fill_notional_ratio=0.1, mid_hist_seed=seed)
                mk = _markout(r.get("fills_log"), sub[SYM]["ots"], mids)
                net = float(r.get("net_usd") or 0.0)
                k = (w, dl)
                tot[k] += net
                n_day[k] += 1
                pos[k] += 1 if net > 0 else 0
                if mk == mk:
                    mk_all[k].append(mk)
                print(f"{day:<12}{a3-a2:>7}{w:>5.0f}{dl/1000:>6.1f}s{r.get('fills'):>6}{net:>+9.3f}"
                      f"{(r.get('net_bp') or 0):>+8.3f}{(r.get('maker_net_bp') or 0):>+7.2f}"
                      f"{(r.get('flatten_net_bp') or 0):>+8.2f}{(r.get('max_dd_pct') or 0):>6.2f}{mk:>+8.2f}")
    print("\n=== criterion: positive days >=5/7 AND total >0 AND mean markout90 >=0 ===")
    for w in widths:
        for dl in delays:
            k = (w, dl)
            mk = float(np.mean(mk_all[k])) if mk_all[k] else float("nan")
            ok = (pos[k] >= 5 and tot[k] > 0 and (mk == mk and mk >= 0)) if n_day[k] else False
            print(f"  w={w:>4.0f} @{dl/1000:.1f}s: days={n_day[k]} positive={pos[k]} "
                  f"total={tot[k]:+.3f}$ markout90={mk:+.2f}bp => {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
