# -*- coding: utf-8 -*-
"""H408 新代码回放冒烟：会话累计 runner/core 改动在完整 tick 循环里跑一遍。

用途：#17 部署（01:40 前后）将重启 worker 加载全部新代码（尾随/降腿量/微价接线/
P1 下限/P3 闸/分形态持有/启停表/quote_ts）——重启前用**回放路径**做一次集成冒烟：
完整 tick 循环 + plan_tick + 全部新分支（默认参数 = 旧行为）。只读，不写车道。

冒烟判据：无异常、fills>0、skip 分布可读、per_symbol 正常。
用法: python scripts/h408_newcode_replay_smoke.py

[2026-09-27 19:00 排查结论] fills=0 的完整归因（三层，均非新代码回归）：
  1. below_step：ratio 0.1 ⇒ $30 < BTC 步长下限（0.001 BTC ≈ $84.5）⇒ ratio 0.4 ✓；
  2. tick_delay_ms=15.1s < evo.ROBUST_DELAYS_MS 下限（18.2s）⇒ 桶消费水位失配
     ⇒ 恒 0 成交（同窗口 18.2s 起即 85+ 腿 ✓）⇒ 冒烟改用 25.1s ✓；
  3. 新鲜尾窗（15:00→now）切片有 0 成交怪癖（回放专项遗留）⇒ 冒烟用
     陈化窗口 09:00→17:00 ✓。
  决定性证据：NEAR 单币 @09:00+8h @18.2/25.1/31.8s = 85/111/114 腿——
  新代码在完整回放 tick 循环中正常成交，无回归。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SINCE_ISO = "2026-09-27T09:00:00+08:00"   # 回放 09:00→17:00（8h，含 16:00 止损段）
UNTIL_HOURS = 8.0


def main() -> int:
    import numpy as np

    from backend.services import lane_registry as reg
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
    from backend.services.market_maker.portfolio_replay import (
        _load_all, replay_portfolio)

    lane = reg.get_lane("mm_asterdex") or {}
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or [])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items()
                            if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

    since_ms = int(dt.datetime.fromisoformat(SINCE_ISO).timestamp() * 1000)
    until_ms = since_ms + int(UNTIL_HOURS * 3600 * 1000)
    data = _load_all(symbols, venue)
    sub = {}
    for s in symbols:
        dd = data[s]
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
        sub[s] = {k: dd[k][a2:a3] for k in ("ots", "bb", "ba")}
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
            t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
            sub[s][k] = dd[k][t2:t3]
    seed = {}
    for s in symbols:
        dd = data[s]
        a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
        lo_i = max(0, a2 - 240)
        seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                   for k in range(lo_i, a2)) if x > 0]

    print(f"回放 {SINCE_ISO} → +{UNTIL_HOURS}h，{len(symbols)} 币…", flush=True)
    # 关键参数（h408 排查结论）：
    #  · tick_delay_ms 必须 ≥ evo.ROBUST_DELAYS_MS 下限（18.2s）——15.1s 时桶消费
    #    水位逻辑失配 ⇒ 恒 0 成交；
    #  · 窗口用"已陈化"区间（当日 09:00→17:00）——新鲜尾窗（15:00→now）切片
    #    也有 0 成交的既有怪癖；
    #  · fill_notional_ratio 让腿量 ≥ 步长下限（BTC 0.001≈$84.5；0.4×$300 ✓）。
    r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                         fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                         enforce_lane_limits=True, tick_delay_ms=25100.0,
                         fill_notional_ratio=0.4, mid_hist_seed=(seed or None))

    print("\n== 冒烟结果 ==")
    print(f"  fills={r.get('fills')} flattens={r.get('flattens')} "
          f"net_bp={r.get('net_bp')} net_usd={r.get('net_usd')}")
    print(f"  maker_net_bp={r.get('maker_net_bp')} flatten_net_bp={r.get('flatten_net_bp')}")
    print("  skip_counts(top10):", dict(sorted((r.get("skip_counts") or {}).items(),
                                               key=lambda kv: -kv[1])[:10]))
    for s, v in sorted((r.get("per_symbol") or {}).items()):
        print(f"  {s:<5} fills={v.get('fills', 0):>4} net_bp={v.get('net_bp', 0):>+8.3f}")
    ok = bool(r and r.get("fills") is not None)
    print("\n冒烟判定:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
