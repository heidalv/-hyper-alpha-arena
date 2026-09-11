from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='120000'"))
    print("### sample symbols for binance 1h")
    for r in c.execute(text("""select symbol, count(*) n from crypto_klines where period='1h' and exchange='binance' group by 1 order by n desc limit 25""")):
        print(r)
    print("\n### asterdex 1h sample")
    for r in c.execute(text("""select symbol, count(*) n from crypto_klines where period='1h' and exchange='asterdex' group by 1 order by n desc limit 25""")):
        print(r)
