from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='120000'"))
    cols=[r[0] for r in c.execute(text("select column_name,data_type from information_schema.columns where table_name='crypto_klines' order by ordinal_position"))]
    print("crypto_klines cols:", cols)
    # 样本
    for r in c.execute(text("select * from crypto_klines where period='4h' and exchange='asterdex' and symbol='BTC' order by timestamp desc limit 2")):
        print(dict(r._mapping))
    print()
    for r in c.execute(text("""select count(*) n, count(*) filter (where amount is not null and amount>0) has_amount,
                                      count(*) filter (where volume is not null and volume>0) has_vol
                               from crypto_klines where period='4h' and exchange='asterdex'""")):
        print(dict(r._mapping))
