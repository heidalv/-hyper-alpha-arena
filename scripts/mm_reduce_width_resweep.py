# -*- coding: utf-8 -*-
"""[F289 2026-09-16] 减仓腿挂宽重扫：`min_width_reduce_bp`（被动回收的激进度）。

依据（F280/F281/F288 的盈亏结构）：两个窗口、两种延迟下 **maker 腿恒正、平仓腿恒负**
（`flatten` 是**减仓腿实现的价格漂移**，不是 taker 手续费——taker 平仓实测仅 3~4 笔/天），
且"成交越多越差"（09-15 关波动闸：46 笔但 net −$0.276、flat −30.9bp）
⇒ 真正的杠杆是**库存回收得多快、以什么价格回收**。`min_width_reduce_bp` 正是
"减仓腿贴多近挂"的旋钮：太宽 ⇒ 库存久留、漂移吃满；太窄 ⇒ 贴价挂单被逆选择。

上次扫它是在**5 币宇宙**（结论 6.0），现在宇宙已收缩为 BTC+ETH 且 F278 步长闸在位
（$30 腿量下 BTC 实际不成交）⇒ 必须重扫。

扫描：`min_width_reduce_bp ∈ {2,4,6（在位）,8,10,14}` × 2 窗口 × 1 延迟（31.8s）+ 快盘夜。
"""
from __future__ import annotations

import dataclasses
import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio  # noqa: E402

LANE = "mm_asterdex"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    syms = list(meta.get("symbols") or ["BTC", "ETH"])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp0 = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    print(f"[F289] universe={syms} 在位 min_width_reduce_bp={qp0.min_width_reduce_bp} "
          f"w_base_bp={qp0.w_base_bp} vol_pause_sigma={lim.vol_pause_sigma}")

    data = _load_all(syms, venue)
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    wins = (("09-15全天", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
            ("快盘夜", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00"),
            ("09-16当日", "2026-09-16T08:00:00+08:00", now_iso))
    vals = (2.0, 4.0, 6.0, 8.0, 10.0, 14.0)
    for wname, s0, s1 in wins:
        since_ms, until_ms = _ms(s0), _ms(s1)
        sub, seed = {}, {}
        for s in syms:
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
        print(f"\n=== {wname} @31.8s ===")
        print(f"{'reduce_bp':>10s} {'fills':>6s} {'net$':>9s} {'net_bp':>8s} {'maker':>7s} "
              f"{'flat':>8s} {'dd%':>6s} {'flat_n':>7s}")
        for v in vals:
            qp = dataclasses.replace(qp0, min_width_reduce_bp=float(v))
            r = replay_portfolio(syms, venue=venue, equity=300.0, params=qp, limits=lim,
                                 fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                 enforce_lane_limits=True, tick_delay_ms=31800.0,
                                 fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            fl_n = sum(1 for f in (r.get("fills_log") or []) if f.get("flatten"))
            tag = " (在位)" if abs(v - float(qp0.min_width_reduce_bp or 0)) < 1e-9 else ""
            print(f"{v:>10.1f} {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                  f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+7.2f} "
                  f"{(r.get('flatten_net_bp') or 0):>+8.2f} {(r.get('max_dd_pct') or 0):>6.2f} "
                  f"{fl_n:>7}{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
