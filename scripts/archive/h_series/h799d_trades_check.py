# -*- coding: utf-8 -*-
"""[h799d] asterdex_trades 数据是否停了(成交窗口 n=0 的原因)。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services.market_maker.attribution import _market_dsn
import psycopg

with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT max(event_ts_ms), count(*) FROM asterdex_trades")
    mx, n = cur.fetchone()
    if mx:
        age = (time.time() - mx / 1000.0) / 60.0
        print(f"asterdex_trades 最新行: {mx} ({age:.1f} 分钟前) | 总行数 {n}")
    else:
        print("asterdex_trades 无数据!")
    cur.execute(
        "SELECT count(*) FROM asterdex_trades"
        " WHERE event_ts_ms > (extract(epoch from now())-3600)*1000")
    print(f"近 1h 行数: {cur.fetchone()[0]}")
    cur.execute(
        "SELECT count(*) FROM asterdex_book_ticker"
        " WHERE event_ts_ms > (extract(epoch from now())-120)*1000")
    print(f"asterdex_book_ticker 近 2 分钟行数: {cur.fetchone()[0]}")
