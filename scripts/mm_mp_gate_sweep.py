# -*- coding: utf-8 -*-
"""[F273 · E2 验收扫描] microprice 偏离闸（mp_block_bp ∈ {0,1,2,4,8}）× 3 窗口。

目标函数 = **净 markout**（调研唯一评估口径）+ 净额不劣。延迟 31.8s，BTC+ETH 宇宙。
验收门槛（调研 §8.3 第 2 步移植）：markout 改善 ≥0.3bp/笔 + 全窗口为正 + 净额不劣；
5% 分位不恶化为硬约束。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from backend.services import lane_registry as reg
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio

LANE = "mm_asterdex"
TAU_MS = [30_000, 90_000]

def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _markout(fills_log: List[dict], data: Dict[str, Dict[str, np.ndarray]]
             ) -> Dict[str, Optional[float]]:
    acc: Dict[int, List[float]] = {t: [] for t in TAU_MS}
    for f in fills_log:
        dd = data.get(f["symbol"])
        if not dd or not len(dd["ots"]):
            continue
        ots, bb, ba = dd["ots"], dd["bb"], dd["ba"]
        ts, px, side = int(f["ts_ms"]), float(f["px"]), f["side"]
        for tau in TAU_MS:
            j = int(np.searchsorted(ots, ts + tau, "left"))
            if j >= len(ots):
                continue
            mid = float((bb[j] + ba[j]) / 2.0)
            if mid <= 0 or px <= 0:
                continue
            mv = (mid - px) if side == "buy" else (px - mid)
            acc[tau].append(mv / px * 1e4)
    out: Dict[str, Optional[float]] = {}
    for tau in TAU_MS:
        vals = sorted(acc[tau])
        n = len(vals)
        out[f"mk{tau // 1000}s"] = round(float(np.mean(vals)), 3) if n else None
        out[f"p05_{tau // 1000}s"] = round(vals[max(0, int(n * 0.05))], 3) if n else None
        out[f"pos_{tau // 1000}s"] = (round(sum(1 for v in vals if v > 0) / n, 3)
                                      if n else None)
    return out


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
    for s in symbols:
        arr = data[s]["mps"]
        print(f"[mps] {s}: n={len(arr)} 非零={int(np.count_nonzero(arr))} "
              f"均值={float(np.mean(arr)):+.3f}bp 绝对值中位={float(np.median(np.abs(arr))):.3f}bp")

    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    windows = [
        ("全天 09-15", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
        ("当日 09-16", "2026-09-16T08:00:00+08:00", now_iso),
        ("快盘夜", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00"),
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
            sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[s][k] = dd[k][t2:t3]
            lo_i = max(0, a2 - 240)
            seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                       for k in range(lo_i, a2)) if x > 0]

        print(f"\n=== {wname}（delay 31.8s）===")
        print(f"{'mp_bp':>6s} {'fills':>6s} {'net_usd':>9s} {'mk30s':>8s} {'p05_30':>7s} "
              f"{'mk90s':>8s} {'p05_90':>7s} {'pos90':>6s} {'dd%':>6s}")
        for mp in (0.0, 0.02, 0.05, 0.1):
            lim2 = LaneRiskLimits(**{**{k: getattr(lim, k) for k in LaneRiskLimits.__dataclass_fields__},
                                     "mp_block_bp": mp})
            r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim2,
                                 fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=31800.0,
                                 fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            mk = _markout(r.get("fills_log") or [], data)
            print(f"{mp:>6.1f} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                  f"{str(mk.get('mk30s')):>8s} {str(mk.get('p05_30s')):>7s} "
                  f"{str(mk.get('mk90s')):>8s} {str(mk.get('p05_90s')):>7s} "
                  f"{str(mk.get('pos_90s')):>6s} {(r.get('max_dd_pct') or 0):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
