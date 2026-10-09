# -*- coding: utf-8 -*-
"""[F309 2026-09-16] Portfolio-level check: does adding ADA hurt LINK? (7 days, anchored baselines)

F307 screened ADA STANDALONE (6/7 days, +1.129$, markout +13.7bp) and the universe was
switched to LINK+ADA. Nobody verified the COMBINED portfolio: the two symbols share the
inventory book and the portfolio caps (max_net_exposure_ratio / max_gross_notional_ratio /
pending limits), so adding ADA could in principle throttle LINK's quoting.

Pre-registered criterion: the combined portfolio must be NON-INFERIOR to LINK-alone on the
same 7 days (total net >= LINK-alone, markout90 >= 0, positive days >= 5/7).
Env: F309_SYMS="LINK" or "LINK,ADA"; F309_W=30
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

LANE, CST = "mm_asterdex", "+08:00"


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
    syms = [s.strip().upper() for s in os.getenv("F309_SYMS", "LINK").split(",") if s.strip()]
    w = float(os.getenv("F309_W", "30"))
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = dataclasses.replace(
        QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__}),
        w_base_bp=w)
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb_all = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    vb_use = {s: float(vb_all[s]) for s in syms if s in vb_all} or None
    data = _load_all(syms, venue)
    days = [(datetime(2026, 9, 10) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    # [F312 2026-09-16] 支持**样本外区间**：日期与天数可由环境变量给出，用于在
    # 冻结配置（LINK+ADA / w=30 / 锚定基准）上做 out-of-sample 复核——只评估、
    # 不再搜索参数（避免在同一批数据上反复扫描造成过拟合）。
    _d0 = os.getenv("F312_START", "")
    if _d0:
        _n = int(os.getenv("F312_DAYS", "7"))
        days = [(datetime.strptime(_d0, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(_n)]
    print(f"[F309] syms={syms} w={w} vb={vb_use} caps: net={lim.max_net_exposure_ratio} "
          f"gross={lim.max_gross_notional_ratio}")
    tot, pos, nd, mks, fills = 0.0, 0, 0, [], 0
    for day in days:
        s0, s1 = _ms(f"{day}T08:00:00{CST}"), _ms(f"{day}T23:59:00{CST}")
        sub, seed, ok = {}, {}, True
        for s in syms:
            dd = data.get(s)
            if not dd:
                ok = False
                break
            a2 = int(np.searchsorted(dd["ots"], s0, "left"))
            a3 = int(np.searchsorted(dd["ots"], s1, "left"))
            t2 = int(np.searchsorted(dd["tts"], s0, "left"))
            t3 = int(np.searchsorted(dd["tts"], s1, "left"))
            if a3 - a2 < 200:
                ok = False
                break
            sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[s][k] = dd[k][t2:t3]
            lo_i = max(0, a2 - 240)
            seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                                   for k in range(lo_i, a2)) if x > 0]
        if not ok:
            print(f"{day}: skipped (data)")
            continue
        r = replay_portfolio(syms, venue=venue, equity=300.0, params=qp, limits=lim,
                             fill_notional=300.0, data=sub, vol_baseline=vb_use,
                             enforce_lane_limits=True, tick_delay_ms=31800.0,
                             fill_notional_ratio=0.1, mid_hist_seed=seed)
        net = float(r.get("net_usd") or 0.0)
        tot += net
        nd += 1
        pos += 1 if net > 0 else 0
        fills += int(r.get("fills") or 0)
        mk_vals = []
        _fl = r.get("fills_log") or []
        for s in syms:
            # [修正] fills_log 是**组合级**的：必须先按 symbol 过滤，否则会拿 A 币的成交
            # 去 B 币的 ots/mids 上取未来价 ⇒ markout 变成天文数字（首版实测 ±2e4bp ✗）
            _fs = [f for f in _fl if str(f.get("symbol")) == s]
            mk_vals.append(_markout(_fs, sub[s]["ots"], (sub[s]["bb"] + sub[s]["ba"]) / 2.0))
        mk = float(np.nanmean(mk_vals)) if any(x == x for x in mk_vals) else float("nan")
        mks.append(mk)
        print(f"{day:<12}{'':>3}fills={r.get('fills'):>3} net={net:>+8.3f}$ "
              f"net_bp={(r.get('net_bp') or 0):>+8.3f} mk90={mk:>+7.2f} "
              f"gross_peak={(r.get('max_gross_usd') or 0):>7.2f} net_peak={(r.get('max_net_usd') or 0):>7.2f}")
    mkavg = float(np.nanmean(mks)) if mks else float("nan")
    print(f"RESULT syms={syms}: days={nd} positive={pos} fills={fills} total={tot:+.3f}$ "
          f"markout90={mkavg:+.2f}bp => "
          f"{'PASS' if (pos >= 5 and tot > 0 and mkavg == mkavg and mkavg >= 0) else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
