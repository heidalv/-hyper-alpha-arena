# -*- coding: utf-8 -*-
"""F389 取证：中线"两闸互斥"在日线 up + 盘中 ranging 下复现。只读。

链路：
  多头 → midlong_location_gate.location_gate_check（仅 ranging/unknown 生效；
          paper 下 24h 分位 ≥70% **硬否决**）；唯一豁免 `_defer_pos` 要求
         `_daily_regime(sym) == "chop"`。
  空头 → midlong_short_regime_block（仅日线下行放行）。
⇒ 若日线=up 且盘中=ranging 且分位≥70%，则多头被位置闸否决、空头被 regime 闸否决 = 无单可开。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_location_gate as LG  # noqa: E402
from backend.services.full_auto.midlong_circuit_gate import (  # noqa: E402
    _daily_regime, _long_mode, _tier_in_learned,
)
from backend.services.kline_data_service import kline_service  # noqa: E402

SYMS = ["ASTER", "UNI", "ZEC", "XRP", "1000PEPE", "SOL", "BNB", "BTC"]

print("=" * 100)
print("F389：位置闸 × 空头 regime 闸 的互斥判定（运行态）")
print("=" * 100)

print(f"\n[开关] location_gate 启用={LG._enabled()}  "
      f"生效 regime={__import__('os').getenv('MIDLONG_LOCATION_REGIMES', 'ranging,unknown')}")
print(f"       追多阈值={LG._f('MIDLONG_LOCATION_MAX_PCT_LONG', 60.0)}%  "
      f"paper 硬否决天花板={LG._paper_shrink_ceiling()}%")
print(f"       long_mode={_long_mode()}   "
      f"defer_to_long_gate={LG._defer_to_long_gate_enabled()}")

print(f"\n{'symbol':10s} {'日线regime':>10s} {'豁免(_defer)':>13s} {'tier在learned':>14s} "
      f"{'24h分位':>9s} {'多头':>10s} {'空头':>10s}")
blocked_both = []
for s in SYMS:
    try:
        reg = _daily_regime(s)
    except Exception as exc:  # noqa: BLE001
        reg = f"<err {type(exc).__name__}>"
    try:
        defer = LG._long_gate_authoritative(s, "mid")
    except Exception:  # noqa: BLE001
        defer = None
    try:
        inlearn = _tier_in_learned("mid")
    except Exception:  # noqa: BLE001
        inlearn = None

    pos = None
    try:
        raw = kline_service.get_aggregated_klines(s, "1h", count=30)
        rows = list(raw)[-24:]
        hi = max(float(r["high"]) for r in rows)
        lo = min(float(r["low"]) for r in rows)
        px = float(rows[-1]["close"])
        pos = (px - lo) / (hi - lo) * 100 if hi > lo else None
    except Exception:  # noqa: BLE001
        pass

    # 多头：盘中 ranging 时位置闸生效
    long_verdict = "?"
    if pos is not None:
        if defer:
            long_verdict = "让位放行"
        elif pos >= (LG._paper_shrink_ceiling() or 101):
            long_verdict = "硬否决"
        elif pos >= LG._f("MIDLONG_LOCATION_MAX_PCT_LONG", 60.0):
            long_verdict = "缩仓放行"
        else:
            long_verdict = "放行"
    # 空头：日线非下行 ⇒ midlong_short_regime_block
    short_verdict = "放行" if str(reg).lower() in ("down", "bear", "downtrend") else "否决"
    if long_verdict == "硬否决" and short_verdict == "否决":
        blocked_both.append(s)
    print(f"{s:10s} {str(reg):>10s} {str(defer):>13s} {str(inlearn):>14s} "
          f"{(f'{pos:.1f}%' if pos is not None else 'n/a'):>9s} "
          f"{long_verdict:>10s} {short_verdict:>10s}")

print(f"\n两方向同时被否的 symbol：{blocked_both if blocked_both else '无'}")
print(f"  计数 = {len(blocked_both)}/{len(SYMS)}")
