from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    cols=c.execute(text("""select column_name,data_type from information_schema.columns where table_name='crypto_klines' order by ordinal_position""")).fetchall()
    print("crypto_klines cols:", cols)
    print()
    for r in c.execute(text("""select exchange, timeframe, count(*) n, count(distinct symbol) syms, min(open_time) mn, max(open_time) mx
                               from crypto_klines group by 1,2 order by 1,2""")):
        print(r)
