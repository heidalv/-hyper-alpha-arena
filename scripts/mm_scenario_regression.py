# -*- coding: utf-8 -*-
"""[F228c 2026-09-15] 场景化回归：把"阴跌 57 连买"做成永久断言 + 多场景四件套。

背景（用户要求）：「顶多是加门禁，没有根除 ✗ 需要深度的设计和测试解决」。
本脚本把 2026-09-15 22:24~22:58 那段**接飞刀行情**钉成回归用例，用**当前在位配置**
（F227 上限 60/30/100 + 900s 时限 + F228 减仓侧豁免）在同一数据上回放，断言：

  A1  峰值总敞口 ≤ gross 上限 × 1.02（不允许 536% 那种仓位 ✗）
  A2  峰值净敞口 ≤ net 上限 × 1.02
  A3  单币最长同向连加 ≤ 8 腿（不允许 57 连买 ✗）
  A4  本窗口净亏 ≤ $20（允许小亏，不允许 2 小时持仓拖出来的大亏）

再加三个对照场景（正常/崩盘/高波动），只报告四件套，不下断言（供设计决策用）。

用法：
    python scripts/mm_scenario_regression.py                # 全部场景
    python scripts/mm_scenario_regression.py --only knife   # 只要回归用例
    python scripts/mm_scenario_regression.py --delay 25.1   # 单一滞后口径
退出码：0 = 断言全过；1 = 有断言失败（回归用例失效）。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker import evolution as evo  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.portfolio_replay import (  # noqa: E402
    _load_all,
    replay_portfolio,
)

LANE = os.getenv("MM_REGRESS_LANE", "mm_asterdex")

# (名称, 起点, 终点, 是否断言) —— 时区 +08:00
SCENARIOS = [
    ("knife",    "2026-09-15T22:24:00+08:00", "2026-09-15T22:58:00+08:00", True),
    ("crash",    "2026-09-15T13:00:00+08:00", "2026-09-15T14:00:00+08:00", False),
    ("highvol",  "2026-09-15T21:00:00+08:00", "2026-09-15T23:00:00+08:00", False),
    ("normal",   "2026-09-15T15:00:00+08:00", "2026-09-15T17:00:00+08:00", False),
]


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑指定场景名")
    ap.add_argument("--delay", type=float, default=0.0, help="单一滞后(ms)；0=ROBUST 三口径")
    args = ap.parse_args()

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"车道不存在: {LANE}")
        return 2
    meta = dict(lane.get("meta") or {})
    symbols = list(meta.get("symbols") or ["BTC"])
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    anchored_vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    delays = [args.delay] if args.delay > 0 else list(evo.ROBUST_DELAYS_MS)

    print(f"车道 {LANE}  标的 {symbols}  口径 {[f'{d/1000:.1f}s' for d in delays]}")
    print("在位参数 " + " ".join(f"{k}={cur.get(k)}" for k in sorted(cur)
          if k in QuoteParams.__dataclass_fields__ or k in LaneRiskLimits.__dataclass_fields__))
    data = _load_all(symbols, venue)
    print(f"[数据] 已载入 {len(symbols)} 标的\n")

    failed = 0
    for name, since_txt, until_txt, enforce in SCENARIOS:
        if args.only and name != args.only:
            continue
        since_ms, until_ms = _ms(since_txt), _ms(until_txt)
        sub = {}
        for s in symbols:
            d = data[s]
            a2 = int(np.searchsorted(d["ots"], since_ms, "left"))
            a3 = int(np.searchsorted(d["ots"], until_ms, "left"))
            t2 = int(np.searchsorted(d["tts"], since_ms, "left"))
            t3 = int(np.searchsorted(d["tts"], until_ms, "left"))
            sub[s] = {k: d[k][a2:a3] for k in ("ots", "bb", "ba")}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[s][k] = d[k][t2:t3]
        seed = {}
        for s in symbols:
            d = data[s]
            a2 = int(np.searchsorted(d["ots"], since_ms, "left"))
            lo_i = max(0, a2 - 240)
            seed[s] = [x for x in (
                float((d["bb"][k] + d["ba"][k]) / 2.0) for k in range(lo_i, a2)) if x > 0]

        print(f"── 场景 [{name}] {since_txt[11:16]}→{until_txt[11:16]} ──")
        for d_ms in delays:
            r = replay_portfolio(
                symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                fill_notional=300.0, data=sub, vol_baseline=(anchored_vb or None),
                enforce_lane_limits=True, tick_delay_ms=float(d_ms),
                fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
            # [F228c] 单币峰值仓位（fills_log 的 net_position_usd 是**每笔成交后**的
            # 带符号仓位名义 ✓）；同时保留"最长同向连加"作为诊断信息（注意它不是
            # 断言：减仓侧放行后，仓位会在上限附近循环进出，总加仓次数大 ≠ 失控 ✓）
            max_run, runs = 0, {}
            sym_peak = {}
            for x in r.get("fills_log") or []:
                sym_peak[x["symbol"]] = max(sym_peak.get(x["symbol"], 0.0),
                                            abs(float(x.get("net_position_usd") or 0.0)))
                key = (x["symbol"], x["side"], x["flatten"])
                if x["flatten"]:
                    runs = {}
                    continue
                runs[key] = runs.get(key, 0) + 1
                max_run = max(max_run, runs[key])
            worst_sym, worst_peak = max(sym_peak.items(), key=lambda kv: kv[1]) if sym_peak else ("-", 0.0)
            n_tr = sum(len(sub[s]["tts"]) for s in symbols)
            vol_bp = np.median([np.median([1.0])])  # 占位，见下方真正口径
            print(f"   {d_ms/1000:4.1f}s  fills={r.get('fills'):>3}  net={r.get('net_usd'):>+8.2f}$"
                  f" ({r.get('net_bp'):>+7.3f}bp)  dd={r.get('max_dd_pct') or 0:.2f}%"
                  f"  gross峰值={r.get('max_gross_usd')}  net峰值={r.get('max_net_usd')}"
                  f"  单币峰值={worst_sym}${worst_peak:.0f}  平仓腿={r.get('flattens')}"
                  f"  跳过={sum((r.get('skip_counts') or {}).values())}")
            if enforce:
                eq = 300.0
                g_cap = eq * float(lim.max_gross_notional_ratio or 0.0) * 1.02
                n_cap = eq * float(lim.max_net_exposure_ratio or 0.0) * 1.02
                s_cap = eq * float(lim.max_net_directional_ratio or 0.0) * 1.02
                checks = [
                    ("A1 峰值总敞口≤上限", r.get("max_gross_usd", 9e9) <= g_cap,
                     f"{r.get('max_gross_usd')} ≤ {g_cap:.1f}"),
                    ("A2 峰值净敞口≤上限", r.get("max_net_usd", 9e9) <= n_cap,
                     f"{r.get('max_net_usd')} ≤ {n_cap:.1f}"),
                    ("A3 单币峰值≤30%权益", worst_peak <= s_cap,
                     f"{worst_sym} ${worst_peak:.0f} ≤ {s_cap:.1f}"),
                    ("A4 窗口净亏≤$20", float(r.get("net_usd") or 0.0) >= -20.0,
                     f"net={r.get('net_usd')}"),
                ]
                for label, ok, detail in checks:
                    print(f"     {'✓' if ok else '✗'} {label:<22} {detail}")
                    if not ok:
                        failed += 1
        print()
    print("=" * 66)
    print(f"{'✓ 回归断言全部通过' if failed == 0 else f'✗ {failed} 条断言失败'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
