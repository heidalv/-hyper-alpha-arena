# -*- coding: utf-8 -*-
"""[F293 2026-09-16] 宽价差标的网格复核（3 窗口 × 2 延迟 × w∈{20,30} × UNI/LINK）+ markout。

F292 首次看到"换标的"的实质空间（09-16 w=20：UNI +$0.449 / LINK +$0.283 vs ETH +$0.007），
但只有 2 窗口 × 1 延迟、成交 8~24 笔 ⇒ 必须扩口径后再谈换宇宙。本脚本：
  · 窗口：09-15 全天 / 快盘夜 / 09-16 当日（数据不足的窗口自动跳过并标注）；
  · 延迟：25.1s + 31.8s（F292 只用 31.8s）；
  · 挂宽：w_base_bp ∈ {20, 30}（F292 显示 w=3/8 全亏 ⇒ 只查宽档）；
  · **markout(90s)**：每笔成交按"成交时刻 +90s 的中价"算 markout bp（买为正=价涨），
    与 F271/F272 同定义；判据 markout ≥ 0 才说明"不是靠逆选择换成交"。
"""
from __future__ import annotations

import dataclasses
import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = "mm_asterdex"
SYMS = ["UNI", "LINK", "ETH"]


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _markout_90(fills, ots, mids) -> float:
    """成交后 90s 的中价 markout（bp，买为正）。τ=90s ≈ 6 个 15s 快照。"""
    vals = []
    for f in fills or []:
        try:
            t = float(f.get("ts") or 0.0) * 1000.0
            side = str(f.get("side") or "")
            px = float(f.get("px") or 0.0)
            if px <= 0:
                continue
            i = int(np.searchsorted(ots, t, "left")) + 6
            if i >= len(mids):
                continue
            m = float(mids[i])
            if m <= 0:
                continue
            bp = (m - px) / px * 1e4 if side == "buy" else (px - m) / px * 1e4
            vals.append(bp)
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
    data = _load_all(SYMS, venue)
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    wins = (("09-15全天", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
            ("快盘夜", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00"),
            ("09-16当日", "2026-09-16T08:00:00+08:00", now_iso))
    print(f"[F293] universe={SYMS} w∈{{20,30}} delays=25.1/31.8s")
    print(f"{'窗口':<10}{'sym':>5}{'w':>5}{'delay':>7}{'fills':>6}{'net$':>9}{'net_bp':>8}"
          f"{'maker':>7}{'flat':>8}{'dd%':>6}{'mk90':>8}")
    for wname, s0, s1 in wins:
        since_ms, until_ms = _ms(s0), _ms(s1)
        for sym in SYMS:
            dd = data.get(sym)
            if not dd:
                print(f"{wname:<10}{sym:>5}  无数据")
                continue
            a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
            a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
            t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
            t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
            if a3 - a2 < 200:
                print(f"{wname:<10}{sym:>5}  数据不足({a3-a2} 快照)")
                continue
            sub = {sym: {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[sym][k] = dd[k][t2:t3]
            mids = (dd["bb"][a2:a3] + dd["ba"][a2:a3]) / 2.0
            lo_i = max(0, a2 - 240)
            seed = {sym: [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                                      for k in range(lo_i, a2)) if x > 0]}
            for w in (20.0, 30.0):
                for dl in (25100.0, 31800.0):
                    qp = dataclasses.replace(qp0, w_base_bp=float(w))
                    r = replay_portfolio([sym], venue=venue, equity=300.0, params=qp, limits=lim,
                                         fill_notional=300.0, data=sub, vol_baseline=None,
                                         enforce_lane_limits=True, tick_delay_ms=float(dl),
                                         fill_notional_ratio=0.1, mid_hist_seed=seed)
                    mk = _markout_90(r.get("fills_log"), sub[sym]["ots"], mids)
                    print(f"{wname:<10}{sym:>5}{w:>5.0f}{dl/1000:>6.1f}s{r.get('fills'):>6} "
                          f"{r.get('net_usd'):>+9.3f}{(r.get('net_bp') or 0):>+8.3f}"
                          f"{(r.get('maker_net_bp') or 0):>+7.2f}{(r.get('flatten_net_bp') or 0):>+8.2f}"
                          f"{(r.get('max_dd_pct') or 0):>6.2f}{mk:>+8.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
