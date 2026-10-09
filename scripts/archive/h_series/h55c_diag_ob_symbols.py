"""诊断 2：`market_orderbook_snapshots(asterdex)` 里的 symbol 到底长什么样？"""
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

    print("=" * 92)
    print("market_orderbook_snapshots(asterdex) 的 symbol 名单 + 每币最新时间")
    print("=" * 92)
    cur.execute(
        "SELECT symbol, count(*) n, max(timestamp) mx, min(timestamp) mn"
        "  FROM market_orderbook_snapshots WHERE exchange='asterdex'"
        " GROUP BY symbol ORDER BY mx DESC"
    )
    rows = cur.fetchall()
    print(f"\n  {'symbol':<20} {'行数':>10} {'最早 ts':>16} {'最新 ts':>16} {'距今(分)':>10}")
    print("  " + "-" * 80)
    import time
    now_ms = int(time.time() * 1000)
    for r in rows:
        age = (now_ms - int(r["mx"])) / 60000.0
        print(f"  {r['symbol']:<20} {r['n']:>10,} {int(r['mn']):>16} {int(r['mx']):>16} {age:>10.1f}")

    print("\n" + "=" * 92)
    print("对照：asterdex_book_ticker 的 symbol 名单")
    print("=" * 92)
    cur.execute(
        "SELECT symbol, count(*) n, max(event_ts_ms) mx FROM asterdex_book_ticker"
        " GROUP BY symbol ORDER BY mx DESC LIMIT 40"
    )
    for r in cur.fetchall():
        age = (now_ms - int(r["mx"])) / 60000.0
        print(f"  {r['symbol']:<20} {r['n']:>12,} 最新 {int(r['mx']):>16}  距今 {age:>8.1f} 分")

    # 若 orderbook_snapshots 里有数据，算真实价差
    cur.execute(
        "SELECT symbol, best_bid::float b, best_ask::float a FROM market_orderbook_snapshots"
        " WHERE exchange='asterdex' AND best_bid>0 AND best_ask>best_bid"
        "   AND timestamp > (extract(epoch from now())*1000)::bigint - 7200000"
    )
    d = cur.fetchall()
    print(f"\n  近 2h orderbook_snapshots(asterdex) 有效行 {len(d):,}")
    if d:
        from collections import defaultdict
        by = defaultdict(list)
        for r in d:
            by[r["symbol"]].append((r["b"], r["a"]))
        print(f"\n  {'symbol':<20} {'n':>8} {'价差 p50 bp':>13} {'价差 中位美元':>15}")
        print("  " + "-" * 62)
        for s, v in sorted(by.items(), key=lambda x: -len(x[1]))[:20]:
            b = np.array([x[0] for x in v]); a = np.array([x[1] for x in v])
            mid = 0.5 * (b + a)
            sp = (a - b) / mid * 1e4
            print(f"  {s:<20} {len(v):>8,} {np.percentile(sp,50):>13.4f} "
                  f"{np.median(a-b):>15.4f}")
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
