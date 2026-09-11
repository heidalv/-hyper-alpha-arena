from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='120000'"))
    for r in c.execute(text("""select exchange, market, count(*) n from crypto_klines where period='1h' group by 1,2 order by n desc limit 12""")):
        print(r)
    print()
    for r in c.execute(text("""select symbol, count(*) n, min(timestamp) mn, max(timestamp) mx from crypto_klines
                               where period='1h' and exchange='binance' and market='perp'
                                 and symbol in ('BTC','ETH','SOL','UNI','XRP','BNB','ASTER','VIRTUAL','LDO','XPL','TIA','AR','ZEC','PUMP')
                               group by 1 order by 1""")):
        print(r)
