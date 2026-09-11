from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for t in ['klines','market_klines','candles']:
        r=c.execute(text("select count(*) from information_schema.tables where table_name=:t"),{"t":t}).scalar()
        print(t, r)
    rows=c.execute(text("""select table_schema,table_name from information_schema.tables
                           where table_name ilike '%kline%' or table_name ilike '%candle%' or table_name ilike '%ohlc%'""")).fetchall()
    print(rows)
