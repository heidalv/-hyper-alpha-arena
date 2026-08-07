# 数据库覆盖统计：各交易所 × 周期 的币种数、最早/最新时间、总根数
from backend.database.connection import MarketSessionLocal
from sqlalchemy import text as T
with MarketSessionLocal() as db:
    rows = db.execute(T("""
        SELECT exchange, period,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(*) AS bars,
               MIN(timestamp) AS min_ts,
               MAX(timestamp) AS max_ts
        FROM crypto_klines
        GROUP BY exchange, period
        ORDER BY exchange, period
    """)).fetchall()
    import datetime
    for r in rows:
        ex, p, sym, bars, min_ts, max_ts = r
        def fmt(ts):
            if ts is None: return "-"
            return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
        print(f"{ex:12s} {p:5s} syms={sym:5d} bars={bars:9d} range={fmt(min_ts)} ~ {fmt(max_ts)}")
