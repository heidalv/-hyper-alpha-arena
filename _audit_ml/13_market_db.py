from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    rows=c.execute(text("""select table_name from information_schema.tables where table_schema='public' order by 1""")).fetchall()
    print("tables:", [r[0] for r in rows])
    for r in rows:
        t=r[0]
        try:
            n=c.execute(text(f'select count(*) from "{t}"')).scalar()
            print(f"  {t}: {n}")
        except Exception as e:
            print(f"  {t}: ERR {str(e)[:60]}")
