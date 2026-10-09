# -*- coding: utf-8 -*-
"""[h840] 索引与查询计划:为什么 20 秒窗口的聚合要 408ms。"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT pg_size_pretty(pg_total_relation_size('asterdex_trades')),"
                " count(*) FROM asterdex_trades")
    sz, n = cur.fetchone()
    print(f"表 asterdex_trades: {sz}, {n:,} 行")
    cur.execute("SELECT indexname, indexdef FROM pg_indexes"
                " WHERE tablename IN ('asterdex_trades','asterdex_book_ticker')")
    print("现有索引:")
    for r in cur.fetchall():
        print(f"  {r[0]}: {r[1][:110]}")
    # 查询计划
    lo = int((time.time() - 20) * 1000)
    cur.execute("EXPLAIN (ANALYZE, BUFFERS) SELECT symbol, min(price), max(price),"
                " sum(CASE WHEN is_buyer_maker THEN qty*price ELSE 0 END)"
                " FROM asterdex_trades WHERE event_ts_ms > %s GROUP BY symbol", (lo,))
    print("查询计划(20s 窗口聚合):")
    for r in cur.fetchall():
        print("  " + r[0][:120])
