# -*- coding: utf-8 -*-
"""[F281 2026-09-16] 出场策略重扫：taker 超时平仓窗口（`max_one_side_seconds`）。

依据（F279/F280 查出的盈亏结构）：两个窗口、两种延迟下**maker 腿恒为正、
flatten 腿恒为负**（09-15 +8.8 / −9.1bp；09-16 +7.6 / −25.8bp），而模型里
"多成交"反而更差（09-15 σ=0 关闸：46 笔但 net −$0.276、flat −30.9bp）
⇒ 亏损集中在**出场**：① 止损、② 持有超时后**打对手价 taker 平仓**（4bp 费 +
穿价差）、③ 减仓腿挂单（被动回收）。②是唯一"定时必然发生"的 taker 成本。

扫描：`max_one_side_seconds ∈ {900, 1800, 3600（在位）, 7200, 86400（≈关）}`，
2 窗口 × 2 延迟，其余取注册表在位配置（含 F278 步长闸、σ=0.7）。
输出：成交数 / 净额$ / net bp / maker bp / **平仓 bp** / 回撤% / 平仓笔数代理
（`fills − 2×往返`不可得，故看 flat_bp 与 net 同向性）。
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
    print(f"[F281] universe={syms} 在位 max_one_side_seconds={lim0.max_one_side_seconds} "
          f"stop_loss_bp={lim0.stop_loss_bp} min_width_reduce_bp={qp.min_width_reduce_bp} "
          f"vol_pause_sigma={lim0.vol_pause_sigma}")

    data = _load_all(syms, venue)
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    wins = (("09-15全天", "2026-09-15T08:00:00+08:00", "2026-09-15T23:59:00+08:00"),
            ("09-16当日", "2026-09-16T08:00:00+08:00", now_iso))
    holds = (900.0, 1800.0, 3600.0, 7200.0, 86400.0)
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
        print(f"{'hold_s':>8s} {'delay':>7s} {'fills':>6s} {'net$':>9s} {'net_bp':>8s} "
              f"{'maker':>7s} {'flat':>8s} {'dd%':>6s} {'quoted':>7s} {'flat_n':>7s}")
        for d in delays:
            for h in holds:
                _kw = {k: getattr(lim0, k) for k in LaneRiskLimits.__dataclass_fields__}
                _kw["max_one_side_seconds"] = h
                lim = LaneRiskLimits(**_kw)
                r = replay_portfolio(syms, venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                                     enforce_lane_limits=True, tick_delay_ms=float(d),
                                     fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
                fl_n = sum(1 for f in (r.get("fills_log") or []) if f.get("flatten"))
                print(f"{h:>8.0f} {d/1000:>6.1f}s {r.get('fills'):>6} {r.get('net_usd'):>+9.3f} "
                      f"{(r.get('net_bp') or 0):>+8.3f} {(r.get('maker_net_bp') or 0):>+7.2f} "
                      f"{(r.get('flatten_net_bp') or 0):>+8.2f} {(r.get('max_dd_pct') or 0):>6.2f} "
                      f"{(r.get('quoted_decisions') or 0):>7} {fl_n:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
