# -*- coding: utf-8 -*-
"""中线关注标的的 K 线新鲜度（**无时区歧义**版本）：用 epoch 整数列比 time.time()。

修正说明：上一版用 `datetime.now(timezone.utc)` 去减 DB 的 **naive 本地时间**（UTC+8）
⇒ 出现"距今 −479 分"这种负值（实际是"1 分钟前"）。本版改用 `crypto_klines.timestamp`
（epoch 秒，无时区歧义）。
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal as S  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "UNI", "ZEC", "ASTER", "VIRTUAL", "DOGE",
        "SUI", "PLAY", "COTI", "ONE", "ICP", "AVAX", "ADA", "XPL", "1000PEPE"]
PERIODS = ["15m", "1h", "4h", "1d"]
# 该周期的"合理最大年龄"（= 3 个周期长度）
MAXH = {"15m": 0.75, "1h": 3.0, "4h": 12.0, "1d": 72.0}

db = S()
try:
    db.execute(text("SET app.is_admin='on'"))
    now = time.time()
    print("=" * 100)
    print(f"crypto_klines 新鲜度（epoch 口径，now={int(now)}）")
    print(f"{'symbol':10s} " + " ".join(f"{p:>13s}" for p in PERIODS))
    print("-" * 100)
    stale = []
    for s in SYMS:
        cells = []
        for per in PERIODS:
            r = db.execute(text("""
                SELECT max(timestamp) FROM crypto_klines
                WHERE symbol = :s AND period = :p
            """), {"s": s, "p": per}).fetchone()
            ts = float(r[0]) if r and r[0] else None
            if not ts:
                cells.append("无数据")
                stale.append((s, per, "无数据"))
                continue
            age_h = (now - ts) / 3600.0
            bad = age_h > MAXH[per]
            if bad:
                stale.append((s, per, f"{age_h:.1f}h"))
            cells.append(("!" if bad else " ") + f"{age_h:.1f}h")
        print(f"{s:10s} " + " ".join(f"{c:>13s}" for c in cells))
    print("-" * 100)
    print("（'!' = 超过 3 个周期；1d 的 ≤72h 视为正常）")
    print(f"\n【过期/缺失清单】共 {len(stale)} 项：")
    for s, per, age in stale:
        print(f"    {s:10s} {per:5s} {age}")

    print("\n=== 交叉核对：有多少标的的 1d 完全缺失（会让 _daily_regime 判不出）===")
    miss_1d = [s for s in SYMS if not db.execute(text(
        "SELECT 1 FROM crypto_klines WHERE symbol=:s AND period='1d' LIMIT 1"),
        {"s": s}).fetchone()]
    print("   1d 无任何数据:", miss_1d or "无")
finally:
    db.close()
