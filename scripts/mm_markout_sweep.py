# -*- coding: utf-8 -*-
"""[F271] markout 目标函数扫描：OFI 毒性闸松紧 × 3 窗口（延迟 31.8s）。

调研（HFT-ML §8.3 第 2 步）要求：任何改动的评估口径 = **净 markout（bp/笔）**，
而不是已实现盈亏。本脚本用 portfolio_replay 新增的 fills_log 逐笔算 markout
（τ = 30s / 90s，30s 快照网格），与 fills/net/dd 并列输出。

扫描维度：ofi_block_threshold ∈ {0.2, 0.3, 0.5(在位), 0.8}（越小=闸越紧，
越大越接近关闭）；闸门语义：上一桶 |OFI| > 阈值 ⇒ 封锁逆势**加仓侧**。
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
    """逐笔 markout（bp）→ {tau: mean}。正=成交价优于后续中价。"""
    acc: Dict[int, List[float]] = {t: [] for t in TAU_MS}
    for f in fills_log:
        s = f["symbol"]
        dd = data.get(s)
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
        vals = acc[tau]
        out[f"mk{tau // 1000}s"] = round(float(np.mean(vals)), 3) if vals else None
        out[f"mk{tau // 1000}s_pos"] = (round(float(np.mean([1.0 if v > 0 else 0.0
                                                             for v in vals])), 3)
                                        if vals else None)
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
            sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba")}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[s][k] = dd[k][t2:t3]
            lo_i = max(0, a2 - 240)
            seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                       for k in range(lo_i, a2)) if x > 0]

        print(f"\n=== {wname}（delay 31.8s）===")
        print(f"{'ofi_thr':>8s} {'fills':>6s} {'net_usd':>9s} {'mk30s':>8s} {'pos30':>6s} "
              f"{'mk90s':>8s} {'pos90':>6s} {'dd%':>6s}")
        for thr in (0.2, 0.3, 0.5, 0.8):
            lim2 = LaneRiskLimits(**{**{k: getattr(lim, k) for k in LaneRiskLimits.__dataclass_fields__},
                                     "ofi_block_threshold": thr})
            r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim2,
                                 fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=31800.0,
                                 fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            mk = _markout(r.get("fills_log") or [], data)
            print(f"{thr:>8.2f} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                  f"{str(mk.get('mk30s')):>8s} {str(mk.get('mk30s_pos')):>6s} "
                  f"{str(mk.get('mk90s')):>8s} {str(mk.get('mk90s_pos')):>6s} "
                  f"{(r.get('max_dd_pct') or 0):>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
