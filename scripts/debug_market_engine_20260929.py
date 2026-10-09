# -*- coding: utf-8 -*-
"""Debug: 后端 market 引擎里 crypto_klines / perp_funding 的实际情况。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import MarketSessionLocal

with MarketSessionLocal() as s:
    r = s.execute(text("SELECT count(*) FROM crypto_klines")).fetchall()
    print("crypto_klines rows:", r)
    r2 = s.execute(text(
        "SELECT symbol, exchange, period, count(*), min(timestamp), max(timestamp) "
        "FROM crypto_klines WHERE symbol='BTC' AND period='1d' GROUP BY 1,2,3"
    )).fetchall()
    print("BTC 1d groups:", r2)
    r3 = s.execute(text("SELECT count(*) FROM perp_funding")).fetchall()
    print("perp_funding rows:", r3)
    r4 = s.execute(text(
        "SELECT symbol, exchange, count(*) FROM perp_funding WHERE symbol='BTC' GROUP BY 1,2"
    )).fetchall()
    print("BTC funding groups:", r4)
