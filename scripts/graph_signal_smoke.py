# -*- coding: utf-8 -*-
"""图信号试点（coin_rank.graph_signal）真实数据冒烟测试。

用法（项目根目录）：
    python scripts/graph_signal_smoke.py

读取本地/生产 MARKET_DATABASE_URL 的 K 线，对 BTC/ETH/SOL/BNB/XPL/VIRTUAL/ASTER
跑 lead/dm 信号并打印覆盖与数值；同时打印 15m/4h 数据深度，用于判断 P0 数据
积累进度（RTGNN 迁移方案 §4.5，训练需要 N≥50 × 6 个月 1h/4h 对齐历史）。
只读，不写任何库。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["COIN_RANK_GRAPH_SIGNAL_ENABLED"] = "true"

from backend.services.coin_rank.graph_signal import (  # noqa: E402
    compute_graph_signals,
    lead_lag_scores,
    momentum_delta_ranks,
    _fetch_klines,
)

SYMS = ["BTC", "ETH", "SOL", "BNB", "XPL", "VIRTUAL", "ASTER"]


def main() -> None:
    t0 = time.time()
    lead = lead_lag_scores(SYMS)
    dm = momentum_delta_ranks(SYMS)
    merged = compute_graph_signals(SYMS)
    dt = time.time() - t0

    print(f"耗时 {dt:.2f}s")
    print(f"lead 覆盖 {len(lead)}/{len(SYMS)}: {lead}")
    print(f"dm   覆盖 {len(dm)}/{len(SYMS)}: {dm}")
    print(f"merged: {merged}")

    for label, period, count in (("15m", "15m", 288), ("4h", "4h", 48)):
        print(f"\n-- {period} 数据深度 --")
        try:
            raw = _fetch_klines(SYMS, period, count)
            for s, r in raw.items():
                print(f"{s}: {len(r)} bars")
        except Exception as e:
            print(f"{period} fetch failed: {e}")


if __name__ == "__main__":
    main()
