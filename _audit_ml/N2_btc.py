from sqlalchemy import create_engine, text
import datetime as dt
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='120000'"))
    rows = c.execute(text("""
        select timestamp, close_price from crypto_klines
        where period='1d' and exchange='asterdex' and symbol='BTC'
          and timestamp >= extract(epoch from timestamp '2026-06-20')::bigint
        order by timestamp
    """)).fetchall()
print("BTC 日线（2026-06-20 起）:")
for ts, cl in rows:
    d=dt.datetime.fromtimestamp(int(ts)).strftime('%m-%d')
    print(f"  {d}  {float(cl):>9.1f}")
