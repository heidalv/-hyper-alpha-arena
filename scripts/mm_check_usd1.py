import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import MarketSessionLocal

with system_identity():
    with MarketSessionLocal() as db:
        # 1) 是否有 USD1 交易对快照
        rows = db.execute(text(
            "SELECT symbol, count(*) AS n, max(timestamp) AS mx "
            "FROM market_orderbook_snapshots WHERE exchange='asterdex' "
            "AND (symbol LIKE '%USD1%') GROUP BY symbol ORDER BY n DESC LIMIT 20"
        )).mappings().all()
        print("USD1 交易对快照:", [(r["symbol"], r["n"]) for r in rows] or "无")
        # 2) BTC/ETH 系列全部交易对（看有哪些本位）
        rows2 = db.execute(text(
            "SELECT symbol, count(*) AS n FROM market_orderbook_snapshots "
            "WHERE exchange='asterdex' AND (symbol LIKE 'BTC%' OR symbol LIKE 'ETH%') "
            "GROUP BY symbol ORDER BY n DESC LIMIT 20"
        )).mappings().all()
        print("BTC/ETH 系列:", [(r["symbol"], r["n"]) for r in rows2])
        # 3) 成交桶里是否有 USD1
        rows3 = db.execute(text(
            "SELECT symbol, count(*) AS n FROM market_trades_aggregated "
            "WHERE exchange='asterdex' AND symbol LIKE '%USD1%' GROUP BY symbol LIMIT 10"
        )).mappings().all()
        print("USD1 成交桶:", [(r["symbol"], r["n"]) for r in rows3] or "无")
