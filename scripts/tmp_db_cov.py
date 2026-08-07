from backend.database.connection import MarketSessionLocal
from sqlalchemy import text
with MarketSessionLocal() as db:
    print("=== 各交易所 × 周期 覆盖（2026-07-26 之后的数据）===")
    rows = db.execute(text("""
        SELECT exchange, period, COUNT(*) AS n,
               COUNT(DISTINCT symbol) AS symbols,
               MAX("timestamp") AS latest,
               (MAX("timestamp") - 1753430400) / 3600.0 AS hours_ago
        FROM crypto_klines
        WHERE "timestamp" > 1753430400
        GROUP BY exchange, period ORDER BY exchange, period
    """)).all()
    for r in rows:
        syms = r[2]
        latest_h = round(r[4] or 0, 1)
        print(f"  {r[0]:12s} {r[1]:4s}: {syms:9d}根 / {r[3]:4d}币 / 最新{r[4]} / {latest_h}h前")
    print("\n=== 各交易所 symbol 数 ===")
    rows = db.execute(text("""
        SELECT exchange, COUNT(DISTINCT symbol) FROM crypto_klines
        WHERE "timestamp" > 1753430400 GROUP BY exchange
    """)).all()
    for r in rows:
        print(f"  {r[0]}: {r[1]} 个 symbol")
    print("\n=== asterdex 各周期缺失的 symbol 样例（BTC/ETH/SOL）===")
    rows = db.execute(text("""
        SELECT period, COUNT(*) FROM crypto_klines
        WHERE exchange='asterdex' AND symbol IN ('BTC','ETH','SOL','BNB','XRP','ADA','DOGE','LINK','AVAX','UNI')
        AND "timestamp" > 1753430400 GROUP BY period
    """)).all()
    for r in rows:
        print(f"  主流币 {r[0]}: {r[1]} 根")
