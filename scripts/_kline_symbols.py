# -*- coding: utf-8 -*-
"""Probe klines for majors: 15m/5m binance depth."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

from backend.database.connection import MarketSessionLocal  # noqa: E402
from backend.database.models import CryptoKline  # noqa: E402
from sqlalchemy import func  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]

with MarketSessionLocal() as db:
    rows = (
        db.query(
            CryptoKline.symbol, CryptoKline.period, CryptoKline.exchange,
            func.count().label("n"),
            func.min(CryptoKline.timestamp).label("t0"),
            func.max(CryptoKline.timestamp).label("t1"),
        )
        .filter(CryptoKline.symbol.in_(SYMS), CryptoKline.period.in_(["15m", "5m", "1h"]))
        .group_by(CryptoKline.symbol, CryptoKline.period, CryptoKline.exchange)
        .order_by(CryptoKline.symbol, CryptoKline.period, func.count().desc())
        .all()
    )
    for r in rows:
        print(f"{r[0]:<6} {r[1]:<6} {r[2]:<16} n={r[3]:>7}  {r[4]} .. {r[5]}")
