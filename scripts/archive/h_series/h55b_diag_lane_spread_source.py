"""诊断：实盘车道读的 `market_orderbook_snapshots(asterdex)` 价差是多少？

`runner.py:1880` 的实盘 tick 路径读的是：

    SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
     WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid
     ORDER BY timestamp DESC LIMIT 1

而 H53b 实测 `asterdex_book_ticker` 的 BTC 价差 p50 = **0.0124bp**。
两者若不一致 ⇒ 实盘用的是**更旧/更粗**的价差 ⇒ F280 的价差相对挂宽会算错基准。

用法：
    .venv\\Scripts\\python.exe scripts\\h55b_diag_lane_spread_source.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("=" * 96)
    print("实盘车道价差来源诊断：market_orderbook_snapshots vs asterdex_book_ticker")
    print("=" * 96)

    cur.execute(
        "SELECT exchange, count(*) n, min(timestamp) mn, max(timestamp) mx,"
        "       count(DISTINCT symbol) nsym"
        "  FROM market_orderbook_snapshots GROUP BY exchange ORDER BY n DESC"
    )
    print("\n  market_orderbook_snapshots 各场地：")
    for r in cur.fetchall():
        print(f"    {r['exchange']:<14} {r['n']:>12,} 行  {r['nsym']:>4} 币  "
              f"ts {r['mn']} ~ {r['mx']}")

    # 车道挂的币（从 lane_registry / 或直接用近 2h 有 tick 的）
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

    print("\n  逐币对照（近 2h，价差 bp = (ask−bid)/mid×1e4）：")
    print(f"\n  {'币':<12} {'orderbook_snap 行数':>20} {'其价差 p50':>11} "
          f"{'book_ticker 价差 p50':>21} {'倍数':>9}")
    print("  " + "-" * 90)

    for s in syms:
        cur.execute(
            "SELECT best_bid::float b, best_ask::float a, timestamp"
            "  FROM market_orderbook_snapshots"
            " WHERE exchange='asterdex' AND symbol=%s AND best_bid>0 AND best_ask>best_bid"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - 7200000",
            (s,),
        )
        ob = cur.fetchall()
        cur.execute(
            "SELECT bid_px::float b, ask_px::float a FROM asterdex_book_ticker"
            " WHERE symbol=%s AND bid_px>0 AND ask_px>bid_px"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000",
            (s,),
        )
        bt = cur.fetchall()
        if not ob or not bt:
            print(f"  {s:<12} {'(无)':>20} {'':>11} {len(bt):>21,}")
            continue
        b1 = np.array([r["b"] for r in ob]); a1 = np.array([r["a"] for r in ob])
        m1 = 0.5 * (b1 + a1); sp1 = (a1 - b1) / m1 * 1e4
        b2 = np.array([r["b"] for r in bt]); a2 = np.array([r["a"] for r in bt])
        m2 = 0.5 * (b2 + a2); sp2 = (a2 - b2) / m2 * 1e4
        p1 = float(np.percentile(sp1, 50)); p2 = float(np.percentile(sp2, 50))
        r = p1 / p2 if p2 > 0 else float("inf")
        print(f"  {s:<12} {len(ob):>20,} {p1:>11.4f} {p2:>21.4f} {r:>8.1f}x")

        # 快照时间跨度（判断网格粒度）
        ts1 = np.array([int(r["timestamp"]) for r in ob])
        if len(ts1) > 2:
            dt = np.diff(np.sort(ts1))
            print(f"  {'':<12} 快照间隔中位 {np.median(dt):.0f}ms   "
                  f"book_ticker 间隔中位 {np.median(np.diff(np.sort(np.array([0])))) if False else 0:.0f}"
                  f"(见 H53b)")

    cn.close()
    print("\n" + "=" * 96)
    print("判读：")
    print("  · 若 orderbook_snapshots 价差 ≈ book_ticker 价差 ⇒ 数据源一致，F280 基准正确")
    print("  · 若 orderbook_snapshots 价差**大很多** ⇒ 实盘用的价差是旧的/粗的，")
    print("    F280 的 spread_mult 会基于错误的基准 ⇒ 必须把实盘 tick 切到 book_ticker")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
