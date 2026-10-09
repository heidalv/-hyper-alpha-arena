import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import market_engine
with market_engine.connect() as c:
    r = c.execute(text("SELECT count(*), min(timestamp), max(timestamp) FROM perp_funding")).first()
    print(f"perp_funding 总行={r[0]}  最早={r[1]}  最新={r[2]}")
    print("--- 按交易所（近 24h 行数 / 最新时间）---")
    sql = """SELECT exchange, count(*) FILTER (WHERE timestamp > :cut) n24, max(timestamp) last_ts,
                    count(distinct symbol) syms
             FROM perp_funding GROUP BY exchange ORDER BY n24 DESC LIMIT 8"""
    import time
    cut = int((time.time() - 86400) * 1000)
    for x in c.execute(text(sql), {"cut": cut}):
        print(f"  {str(x[0]):12s} n24={x[1]:<8} last={x[2]} symbols={x[3]}")
    print("--- 抽样最近 5 行 ---")
    for x in c.execute(text("SELECT exchange, symbol, timestamp, funding_rate FROM perp_funding ORDER BY timestamp DESC LIMIT 5")):
        print(f"  {x[0]:10s} {x[1]:10s} ts={x[2]} rate={x[3]}")
