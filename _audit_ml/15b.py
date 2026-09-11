from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='60000'"))
    print("### distinct periods")
    for r in c.execute(text("select period, count(*) from crypto_klines group by 1 order by 1")):
        print(r)
