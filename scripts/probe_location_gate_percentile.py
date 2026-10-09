# -*- coding: utf-8 -*-
"""位置闸"24h 区间分位"是否系统性偏高（F388 取证）。只读。

对照：日志里闸门读到的分位 vs 用**真实 1h K 线（含当前 bar）**算出的分位。
若真实分位 <70% 而闸门读 >70%，说明闸门用的区间上界是**旧的**（不含当前价）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYMS = ["ASTER", "UNI", "ZEC", "XRP", "1000PEPE", "SOL", "BNB"]

from backend.services.kline_data_service import kline_service  # noqa: E402
from backend.services.full_auto import midlong_location_gate as LG  # noqa: E402

print("=" * 96)
print("位置闸分位对照：真实 1h K 线（含当前 bar） vs 闸门内部口径")
print("=" * 96)
print(f"{'symbol':10s} {'现价':>12s} {'24h高':>12s} {'24h低':>12s} "
      f"{'真实分位':>9s} {'末根high':>12s} {'除末根外分位':>12s}")

for s in SYMS:
    try:
        raw = kline_service.get_aggregated_klines(s, "1h", count=40)
        if not raw or len(raw) < 5:
            print(f"{s:10s} 无 K 线")
            continue
        rows = list(raw)[-24:]
        hi = max(float(r["high"]) for r in rows)
        lo = min(float(r["low"]) for r in rows)
        last_close = float(rows[-1]["close"])
        last_high = float(rows[-1]["high"])
        pos_all = (last_close - lo) / (hi - lo) * 100 if hi > lo else float("nan")
        # 若区间**不含最后一根**（模拟"高点是旧的"）
        rows_ex = rows[:-1]
        hi_ex = max(float(r["high"]) for r in rows_ex)
        lo_ex = min(float(r["low"]) for r in rows_ex)
        pos_ex = (last_close - lo_ex) / (hi_ex - lo_ex) * 100 if hi_ex > lo_ex else float("nan")
        print(f"{s:10s} {last_close:12.6g} {hi:12.6g} {lo:12.6g} "
              f"{pos_all:8.1f}% {last_high:12.6g} {pos_ex:11.1f}%")
    except Exception as exc:  # noqa: BLE001
        print(f"{s:10s} 失败: {type(exc).__name__}: {str(exc)[:70]}")

print("\n[闸门阈值]")
print(f"  追高天花板 MIDLONG_LOCATION_PAPER_SHRINK_CEILING = "
      f"{LG._paper_shrink_ceiling()}")
print(f"  paper 缩仓开关 = {LG._paper_shrink_enabled()}  倍数 = {LG._paper_shrink_mult()}")
print(f"  1h K 兜底缓存 TTL = {LG._RANGE_TTL_SEC}s")
