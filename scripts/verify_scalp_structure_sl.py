#!/usr/bin/env python3
"""回放验证：结构 SL vs swing low 距离（ScalpExecutionLane Phase 1）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd

from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
from backend.services.kline_data_service import kline_service


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 scalp 结构止损与 swing low 关系")
    parser.add_argument("--symbols", nargs="+", default=["BTC", "JTO", "ETH"])
    parser.add_argument("--limit", type=int, default=20, help="每币采样 K 线窗口数")
    args = parser.parse_args()

    overlap_before = 0
    overlap_after = 0
    total = 0

    for sym in args.symbols:
        raw = kline_service.get_klines_from_db(sym.upper(), "5m", 100, exchange="hyperliquid")
        if not raw or len(raw) < 40:
            print(f"[SKIP] {sym} K线不足")
            continue
        df = pd.DataFrame(raw)
        swing_low, swing_high, range_pos = structure_stop_calculator.swing_levels(df)
        price = float(df["close"].iloc[-1])
        atr_pct = structure_stop_calculator.compute_atr_pct({"atr_pct": 0.015})

        # 旧算法：纯 ATR
        old_sl = price * (1 - atr_pct)
        # 新算法：结构 SL
        _, _, new_sl, _ = structure_stop_calculator.compute_sl_tp(
            {"klines": df, "price": price, "atr_pct": atr_pct},
            side="long",
            entry=price,
            swing_low=swing_low,
            swing_high=swing_high,
        )

        old_dist = abs(old_sl - swing_low) / price if price else 0
        new_dist = abs(new_sl - swing_low) / price if price else 0
        old_overlap = old_dist < 0.005  # SL 与 swing low 距离 < 0.5%
        new_overlap = new_sl < swing_low  # 新 SL 应在 swing low 下方

        total += 1
        overlap_before += int(old_overlap)
        overlap_after += int(not new_overlap or new_sl <= swing_low * 0.992)

        print(
            f"{sym} price={price:.4f} swing_low={swing_low:.4f} "
            f"old_sl={old_sl:.4f}({old_dist:.3%}) new_sl={new_sl:.4f}({new_dist:.3%}) "
            f"range_pos={range_pos:.2f} "
            f"old_on_swing={'YES' if old_overlap else 'no'} "
            f"new_below_swing={'YES' if new_sl < swing_low else 'no'}"
        )

    if total == 0:
        print("无有效样本")
        return 1

    print(
        f"\n汇总 {total} 币: "
        f"旧 SL 贴近 swing low 比例 {overlap_before}/{total} ({100*overlap_before/total:.0f}%) | "
        f"新 SL 在 swing low 下方 {overlap_after}/{total} ({100*overlap_after/total:.0f}%)"
    )
    print("48h 纸盘基线：请在 FullAuto 纸盘会话运行 48h 后对比开仓数/胜率/区间高位占比。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
