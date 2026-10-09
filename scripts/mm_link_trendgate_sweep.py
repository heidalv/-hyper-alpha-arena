# -*- coding: utf-8 -*-
"""[F302 2026-09-16] LINK trend-gate re-standardisation sweep (7 days, anchored baseline).

Finding (F301): LIVE LINK segment quotes ONE side in 67% of decisions
(skip: ct_trend_down 9 + trend_up 6 out of 24 ticks). LINK's vol baseline is 3.2984bp
vs ETH 2.0083bp (+64%), but the trend / counter-trend gates use ABSOLUTE bp thresholds
tuned on ETH -> they fire far more often on LINK. The F294/F295/F299 model evidence was
produced with the SAME (tight) gates, so relaxing them is potential UPSIDE, not a bug fix.

Pre-registered criterion (unchanged): positive days >=5/7 AND total >0 AND mean markout90 >=0
AND total must beat the incumbent +2.096$ (F299, anchored baseline, w=30, both gates at
their live values). ASCII-only file on purpose (PowerShell mangles non-ASCII sources).

Env: F302_TP="0,15" F302_STM="0,20"  (trend_pause_bp / side_trend_min_bp)
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

LANE, SYM, CST = "mm_asterdex", "LINK", "+08:00"
INCUMBENT = 2.096


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
            if m > 0:
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
    lim0 = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    vb_use = {SYM: float(vb[SYM])} if SYM in vb else None
    dd = _load_all([SYM], venue).get(SYM)
    if not dd:
        print("no data")
        return 1
    tps = [float(x) for x in (os.getenv("F302_TP", "0,15")).split(",") if x]
    stms = [float(x) for x in (os.getenv("F302_STM", "0,20")).split(",") if x]
    days = [(datetime(2026, 9, 10) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    print(f"[F302] incumbent: trend_pause_bp={lim0.trend_pause_bp} "
          f"side_trend_min_bp={qp0.side_trend_min_bp} -> +{INCUMBENT}$ (F299) ; "
          f"vb={vb_use} w={qp0.w_base_bp}")
    print(f"{'tp':>5}{'stm':>6}{'days':>6}{'fills':>7}{'net$':>9}{'net_bp':>8}{'maker':>7}"
          f"{'flat':>8}{'pos/7':>7}{'mk90':>8}  verdict")
    for tp in tps:
        for stm in stms:
            lim = dataclasses.replace(lim0, trend_pause_bp=float(tp))
            qp = dataclasses.replace(qp0, side_trend_min_bp=float(stm))
            tot, pos, nfill, mks = 0.0, 0, 0, []
            ndays = 0
            for day in days:
                s0, s1 = _ms(f"{day}T08:00:00{CST}"), _ms(f"{day}T23:59:00{CST}")
                a2 = int(np.searchsorted(dd["ots"], s0, "left"))
                a3 = int(np.searchsorted(dd["ots"], s1, "left"))
                t2 = int(np.searchsorted(dd["tts"], s0, "left"))
                t3 = int(np.searchsorted(dd["tts"], s1, "left"))
                if a3 - a2 < 200:
                    continue
                sub = {SYM: {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}}
                for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                    sub[SYM][k] = dd[k][t2:t3]
                mids = (dd["bb"][a2:a3] + dd["ba"][a2:a3]) / 2.0
                lo_i = max(0, a2 - 240)
                seed = {SYM: [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                                          for k in range(lo_i, a2)) if x > 0]}
                r = replay_portfolio([SYM], venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=vb_use,
                                     enforce_lane_limits=True, tick_delay_ms=31800.0,
                                     fill_notional_ratio=0.1, mid_hist_seed=seed)
                net = float(r.get("net_usd") or 0.0)
                tot += net
                ndays += 1
                pos += 1 if net > 0 else 0
                nfill += int(r.get("fills") or 0)
                mk = _markout(r.get("fills_log"), sub[SYM]["ots"], mids)
                if mk == mk:
                    mks.append(mk)
            mkavg = float(np.mean(mks)) if mks else float("nan")
            ok = (pos >= 5 and tot > 0 and mkavg == mkavg and mkavg >= 0 and tot > INCUMBENT)
            print(f"{tp:>5.0f}{stm:>6.0f}{ndays:>6}{nfill:>7}{tot:>+9.3f}{'':>8}{'':>7}"
                  f"{'':>8}{pos:>4}/{ndays:<2}{mkavg:>+8.2f}  {'BETTER' if ok else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
