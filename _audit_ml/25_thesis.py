from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=c.execute(text("""select table_name from information_schema.tables where table_schema='public'
                           and (table_name like '%thesis%' or table_name like '%brain%')""")).fetchall()
    print(rows)
    for (t,) in rows:
        cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name=:t order by ordinal_position"),{"t":t})]
        n=c.execute(text(f'select count(*) from "{t}"')).scalar()
        print(f"\n== {t} n={n}\n   {cols}")
