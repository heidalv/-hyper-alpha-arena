# -*- coding: utf-8 -*-
"""[F248] 深度档位立项：先盘清现状——深度列是否已采集？raw_levels 里有什么？"""
import json
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

with MarketSessionLocal() as db:
    print("== 最近 3 条 BTC 快照的深度列 ==")
    for r in db.execute(text(
        "SELECT timestamp, best_bid, best_ask, bid_depth_5, ask_depth_5,"
        " bid_depth_10, ask_depth_10, raw_levels"
        " FROM market_orderbook_snapshots"
        " WHERE exchange='asterdex' AND symbol='BTC'"
        " ORDER BY timestamp DESC LIMIT 3")).mappings().all():
        d = dict(r)
        raw = d.pop("raw_levels")
        print("  ", d)
        print("   raw_levels:", (str(raw)[:300] if raw else None))
    print("== 深度列非零比例（BTC 近 1000 条）==")
    for r in db.execute(text(
        "SELECT COUNT(*) n,"
        " SUM(CASE WHEN bid_depth_5>0 THEN 1 ELSE 0 END) d5,"
        " SUM(CASE WHEN bid_depth_10>0 THEN 1 ELSE 0 END) d10,"
        " SUM(CASE WHEN raw_levels IS NOT NULL AND raw_levels<>'' THEN 1 ELSE 0 END) rl"
        " FROM market_orderbook_snapshots"
        " WHERE exchange='asterdex' AND symbol='BTC'"
        " ORDER BY timestamp DESC LIMIT 1000")).mappings().first() if False else None:
        pass
    r = db.execute(text(
        "SELECT COUNT(*) n,"
        " SUM(CASE WHEN bid_depth_5>0 THEN 1 ELSE 0 END) d5,"
        " SUM(CASE WHEN bid_depth_10>0 THEN 1 ELSE 0 END) d10,"
        " SUM(CASE WHEN raw_levels IS NOT NULL AND raw_levels<>'' THEN 1 ELSE 0 END) rl"
        " FROM (SELECT * FROM market_orderbook_snapshots"
        "       WHERE exchange='asterdex' AND symbol='BTC'"
        "       ORDER BY timestamp DESC LIMIT 1000) x")).mappings().first()
    print("  ", dict(r))
