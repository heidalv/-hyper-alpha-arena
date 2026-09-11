# -*- coding: utf-8 -*-
"""Z133: 真实建仓口径 vs 组合闸估算口径（§60 的决定性对照）。

用 `PositionConstruction.construct(...)`（真实建仓路径用的构造函数）算出 mid 车道
在 equity=$4,700 时的**实际名义**，与组合闸喂进去的 `estimate_open_notional()`
（equity×margin×leverage）对比。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import backend.services.position_construction as pc  # noqa: E402
from backend.services.mlto.midlong_portfolio_risk import (  # noqa: E402
    estimate_open_notional, estimate_open_notional_aligned,
)

EQ = 4700.0
lim = pc.LaneLimits.for_lane("mid")
print("=== LaneLimits.for_lane('mid') ===")
print("  ", lim.to_dict() if hasattr(lim, "to_dict") else lim)

for bw in (0.10, 0.30):
    try:
        plan = pc.construct(
            lane="mid", symbol="ETH", equity=EQ, price=3000.0, realized_vol=0.6,
            stop_distance_pct=0.045, base_weight=bw,
            symbol_open_notional=0.0, cluster_open_notional=0.0, lane_open_notional=0.0,
            limits=lim,
        )
        d = plan.to_dict() if hasattr(plan, "to_dict") else plan
        notional = float(getattr(plan, "notional", 0) or (d.get("notional") if isinstance(d, dict) else 0) or 0)
        print(f"\n=== construct(base_weight={bw}) ===")
        print(f"   notional=${notional:.0f} ({notional/EQ:.1%} 权益) ok={getattr(plan,'ok',None)} "
              f"caps={getattr(plan,'caps_applied',None)}")
    except Exception as e:
        print(f"\n=== construct(base_weight={bw}) 失败: {type(e).__name__}: {str(e)[:150]}")

legacy = estimate_open_notional(equity=EQ, margin_frac=0.15, leverage=10.0)
aligned = estimate_open_notional_aligned(equity=EQ, sl_pct=0.045, risk_pct=0.0075)
print("\n=== 口径对照（equity=$4,700, mid, SL=4.5%）===")
print(f"  组合闸输入 estimate_open_notional(margin 0.15 × 10x) = ${legacy:.0f} ({legacy/EQ:.0%} 权益)")
print(f"  同口径估计 estimate_open_notional_aligned            = ${aligned:.0f} ({aligned/EQ:.0%} 权益)")
print(f"  倍差 = {legacy/aligned:.1f}x")
print(f"  净敞口上限 = 150% 权益 = ${1.5*EQ:.0f}")
