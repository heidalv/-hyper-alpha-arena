import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import MarketSessionLocal

RANGES = [
    ("09-15 全天", 1789430400000, 1789487940000),
    ("今日 08:00+", 1789516800000, 1789536000000),
]
with system_identity():
    with MarketSessionLocal() as db:
        for name, a, b in RANGES:
            r = db.execute(text(
                "SELECT count(*) AS n, count(bid_depth_5) AS d5, count(raw_levels) AS raw, "
                "count(bid_depth_10) AS d10 "
                "FROM market_orderbook_snapshots WHERE exchange='asterdex' AND symbol='BTC' "
                "AND timestamp BETWEEN :a AND :b"
            ), {"a": a, "b": b}).mappings().first()
            print(f"{name}: n={r['n']} bid_depth_5 非空={r['d5']} depth_10={r['d10']} raw={r['raw']}")
