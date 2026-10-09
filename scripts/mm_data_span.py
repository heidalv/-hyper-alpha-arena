# -*- coding: utf-8 -*-
"""[F238] 数据跨度检查：market_trades_aggregated 覆盖多少天？能跑几天的回放？"""
import sys
import time

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402


def t(ms):
    return time.strftime("%m-%d %H:%M", time.localtime(ms / 1000))


with MarketSessionLocal() as db:
    r = db.execute(text(
        "SELECT MIN(timestamp) t0, MAX(timestamp) t1, COUNT(*) n"
        " FROM market_trades_aggregated WHERE exchange='asterdex'")).mappings().first()
    print(f"trades: {t(r['t0'])} → {t(r['t1'])}  rows={r['n']}")
    r = db.execute(text(
        "SELECT MIN(timestamp) t0, MAX(timestamp) t1 FROM market_orderbook_snapshots"
        " WHERE exchange='asterdex'")).mappings().first()
    print(f"snaps:  {t(r['t0'])} → {t(r['t1'])}")
    print("== 按天成交桶数（asterdex, BTC）==")
    for row in db.execute(text(
        "SELECT to_timestamp((timestamp/1000)::bigint)::date d, COUNT(*) n"
        " FROM market_trades_aggregated WHERE exchange='asterdex' AND symbol='BTC'"
        " GROUP BY 1 ORDER BY 1")).mappings().all():
        print("  ", row["d"], row["n"])
