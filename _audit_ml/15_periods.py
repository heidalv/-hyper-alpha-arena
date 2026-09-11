from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    print("### period x exchange coverage")
    for r in c.execute(text("""select period, exchange, count(*) n, count(distinct symbol) syms,
                                      to_timestamp(min(timestamp)) mn, to_timestamp(max(timestamp)) mx
                               from crypto_klines group by 1,2 order by 1,2""")):
        print(r)
    print("\n### symbols per period (top)")
    for r in c.execute(text("""select period, count(distinct symbol) syms from crypto_klines group by 1 order by 1""")):
        print(r)
