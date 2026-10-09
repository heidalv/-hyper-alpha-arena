# -*- coding: utf-8 -*-
"""[F241] 薄价差日机制分析：每天的市场自身价差（相对价差 bp） vs 回放的被动边际。
假设：被动边际 ≈ 挂单价差捕获 − 逆选择，且受**市场自身价差 regime**支配——
市场价差薄时固定 8bp 半宽离盘口太远 ⇒ 能成交的只有"挑人"的逆选择单 ✗。
只读。
"""
import sys
import time

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

DAYS = ["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15"]
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]


def t(ms):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


with MarketSessionLocal() as db:
    print(f"{'日':<12}{'快照数':>8}{'价差bp中位':>12}{'价差bp均值':>12}{'价差bp P90':>12}")
    for d in DAYS:
        t0 = int(time.mktime(time.strptime(d + " 00:00:00", "%Y-%m-%d %H:%M:%S")) * 1000)
        t1 = t0 + 86400_000
        # 全 5 币的相对价差分布（每快照一条）
        rows = db.execute(text(
            "SELECT (best_ask-best_bid)/mid*1e4 AS sp_bp FROM ("
            "  SELECT (best_bid+best_ask)/2.0 AS mid, best_bid, best_ask"
            "  FROM market_orderbook_snapshots"
            "  WHERE exchange='asterdex' AND symbol = ANY(:syms)"
            "    AND best_bid>0 AND best_ask>best_bid"
            "    AND timestamp >= :a AND timestamp < :b) x"
        ), {"syms": SYMS, "a": t0, "b": t1}).fetchall()
        vals = sorted(float(r[0]) for r in rows)
        if not vals:
            print(f"{d:<12} 无数据")
            continue
        n = len(vals)
        med = vals[n // 2]
        mean = sum(vals) / n
        p90 = vals[int(n * 0.9)]
        print(f"{d:<12}{n:>8}{med:>12.2f}{mean:>12.2f}{p90:>12.2f}")
    # 分币看 09-12 vs 09-10（薄价差日 vs 好日子）
    print()
    for d in ("2026-09-10", "2026-09-12", "2026-09-13", "2026-09-14"):
        t0 = int(time.mktime(time.strptime(d + " 00:00:00", "%Y-%m-%d %H:%M:%S")) * 1000)
        t1 = t0 + 86400_000
        row = db.execute(text(
            "SELECT symbol, "
            "  percentile_cont(0.5) WITHIN GROUP (ORDER BY"
            "    (best_ask-best_bid)/((best_bid+best_ask)/2.0)*1e4) med"
            " FROM market_orderbook_snapshots"
            " WHERE exchange='asterdex' AND symbol = ANY(:syms)"
            "   AND best_bid>0 AND best_ask>best_bid"
            "   AND timestamp >= :a AND timestamp < :b"
            " GROUP BY symbol ORDER BY symbol"
        ), {"syms": SYMS, "a": t0, "b": t1}).fetchall()
        print(d, "  ".join(f"{r[0]}={float(r[1]):.2f}" for r in row))
