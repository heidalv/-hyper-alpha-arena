# -*- coding: utf-8 -*-
"""[F292 2026-09-16] 宽价差标的模型 A/B（ADA/UNI/AVAX/LINK vs ETH）。

依据：F290 盘口普查 —— 中位对手盘价差 **ADA 5.15bp / UNI 4.73 / AVAX 4.11 / LINK 2.76
vs ETH 0.04bp**（70~130×）；F291 exchangeInfo 实测这四只 **$30 腿全部可下单** ✓。
⇒ 同样的做市逻辑在它们身上"每次成交可赚的价差"天然高两个数量级。

**不能直接搬 ETH 的 `w_base_bp=12`**：ETH 对手盘价差只有 0.04bp（=1 tick），12bp 是
"离盘口 ~300 tick"；而这些币价差本身就有 3~5bp ⇒ 挂宽要按各自价差重标定。
本脚本每币扫 `w_base_bp ∈ {3, 8, 20}`（对照 ETH 在 12 的在位值），2 窗口，单币组合。

输出：每币×每挂宽 的 成交数 / 净额$ / net bp / maker bp / flat bp / 回撤% / 报价决策数。
判据：若某币在**任何**挂宽下 net bp 显著高于 ETH 同窗口水平（ETH：09-16 −6.3bp），
则值得进入下一步（审计换宇宙）；否则"换标的"路线也排除。
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
CAND = ["ADA", "UNI", "AVAX", "LINK", "ETH"]


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def main() -> int:
    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    venue = str(meta.get("venue") or "asterdex")
    cur = dict(meta.get("params") or {})
    qp0 = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
    vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    data = _load_all(CAND, venue)
    now_iso = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    wins = (("快盘夜", "2026-09-15T22:05:00+08:00", "2026-09-16T02:00:00+08:00"),
            ("09-16当日", "2026-09-16T08:00:00+08:00", now_iso))
    errs = []
    for wname, s0, s1 in wins:
        since_ms, until_ms = _ms(s0), _ms(s1)
        for sym in CAND:
            dd = data.get(sym)
            if not dd:
                errs.append(sym)
                continue
            a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
            a3 = int(np.searchsorted(dd["ots"], until_ms, "left"))
            t2 = int(np.searchsorted(dd["tts"], since_ms, "left"))
            t3 = int(np.searchsorted(dd["tts"], until_ms, "left"))
            if a3 - a2 < 50:
                errs.append(f"{sym}(数据少 {a3-a2})")
                continue
            sub = {sym: {k: dd[k][a2:a3] for k in ("ots", "bb", "ba", "mps")}}
            for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
                sub[sym][k] = dd[k][t2:t3]
            lo_i = max(0, a2 - 240)
            seed = {sym: [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
                                      for k in range(lo_i, a2)) if x > 0]}
            print(f"\n=== {wname} · {sym} ===")
            for w in (3.0, 8.0, 20.0):
                qp = dataclasses.replace(qp0, w_base_bp=float(w))
                # [修正] 注册表里的 `replay_baseline.vol_baseline_bp` 只锚了旧 5 币，
                # 新候选（ADA/UNI/AVAX/LINK）没有锚值 ⇒ 直接传 dict 会 KeyError。
                # 这里统一传 None：由回放按**同一估计口径**从窗口现算各币基准，
                # 保证"逐币对比"内部一致（跨窗口比较时记住口径差异即可）。
                r = replay_portfolio([sym], venue=venue, equity=300.0, params=qp, limits=lim,
                                     fill_notional=300.0, data=sub, vol_baseline=None,
                                     enforce_lane_limits=True, tick_delay_ms=31800.0,
                                     fill_notional_ratio=0.1, mid_hist_seed=seed)
                print(f"  w={w:>4.1f} fills={r.get('fills'):>4} net={r.get('net_usd'):>+8.3f}$ "
                      f"net_bp={(r.get('net_bp') or 0):>+8.3f} maker={(r.get('maker_net_bp') or 0):>+7.2f} "
                      f"flat={(r.get('flatten_net_bp') or 0):>+8.2f} dd={(r.get('max_dd_pct') or 0):>5.2f}% "
                      f"quoted={r.get('quoted_decisions')}")
    if errs:
        print("\n数据缺失:", sorted(set(errs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
