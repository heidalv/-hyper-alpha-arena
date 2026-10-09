# -*- coding: utf-8 -*-
"""[F280 2026-09-16] 波动闸重扫（收缩后宇宙 + F278 步长闸在位）。

动机：F279 的 A/B 顺带查出**最大的一类"不报价"是波动闸**——
模型 09-15 全天 `vol_pause` 1610 次 vs 报价 1865 次（≈46% 决策），双边同时挂
（`both`）只占 12~14% ⇒ 车道大多数时间不是双边在做市。而"波动闸阈值/窗口"
上一次扫是在**5 币宇宙**里做的（当时结论 0.7 为局部最优）；现在宇宙已收缩为
BTC+ETH（$30 腿量下 BTC 被 F278 判 `below_step`、实际只剩 ETH），必须重扫。

扫描：`vol_pause_sigma ∈ {0（关）, 0.3, 0.5, 0.7（在位）, 1.0, 1.5}`，
2 窗口 × 2 延迟，其余一律取**注册表在位配置**（含 F278 闸）。
输出：成交数 / 净额$ / net bp / maker bp / 平仓 bp / 回撤% / 报价决策数 /
`both%` / `vol_pause` 次数 / σ 均值。
"""
from __future__ import annotations

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
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim0 = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    print(f"[F280] universe={syms} venue={venue} 在位 vol_pause_sigma={lim0.vol_pause_sigma} "
          f"vol_window={lim0.vol_window} w_base_bp={qp.w_base_bp} baseline={vb}")

    data = _load_all(syms, venue)
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    wins = (("09-15全天", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
            ("09-16当日", "2026-09-16T08:00:00+08:00", now_iso))
    sigmas = (0.0, 0.3, 0.5, 0.7, 1.0, 1.5)
    delays = (31800.0, 18200.0)

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
        print(f"\n=== {wname} ===")
        print(f"{'sigma':>6s} {'delay':>7s} {'fills':>6s} {'net$':>9s} {'net_bp':>8s} "
              f"{'maker':>7s} {'flat':>8s} {'dd%':>6s} {'quoted':>7s} {'both%':>6s} "
              f"{'vpause':>7s} {'flow':>6s} {'sig':>6s}")
        for d in delays:
            for sg in sigmas:
                lim = LaneRiskLimits(**{**{k: getattr(lim0, k)
                                          for k in LaneRiskLimits.__dataclass_fields__},
                                        "vol_pause_sigma": sg})
                r = replay_portfolio(syms, venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                     enforce_lane_limits=True, tick_delay_ms=float(d),
                                     fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
                sc = r.get("skip_counts") or {}
                sd = r.get("side_counts") or {}
                dec = max(1, sum(sd.values()))
                print(f"{sg:>6.1f} {d/1000:>6.1f}s {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                      f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+7.2f} "
                      f"{(r.get('flatten_net_bp') or 0):>+8.2f} {(r.get('max_dd_pct') or 0):>6.2f} "
                      f"{(r.get('quoted_decisions') or 0):>7} {(sd.get('both', 0)/dec*100):>5.1f}% "
                      f"{sc.get('vol_pause', 0):>7} {sc.get('flow_persist', 0):>6} "
                      f"{(r.get('avg_sigma') or 0):>6.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
