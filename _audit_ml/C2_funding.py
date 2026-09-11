from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='paper_funding_ledger' order by ordinal_position"))]
    print("cols:", cols)
    print("count:", c.execute(text("select count(*) from paper_funding_ledger")).scalar())
    for r in c.execute(text("select * from paper_funding_ledger order by id desc limit 5")):
        print(dict(r._mapping))
    print()
    for r in c.execute(text("select account_id, count(*) n, round(sum(payment)::numeric,4) total from paper_funding_ledger group by 1 order by 1")):
        print(dict(r._mapping))
