from sqlalchemy import create_engine, text
for db in ("alpha_arena","alpha_analytics","alpha_market"):
    try:
        eng=create_engine(f"postgresql+psycopg://laobao:alpha_pass@localhost:5432/{db}")
        with eng.connect() as c:
            n=c.execute(text("select count(*) from information_schema.tables where table_name='brain_attribution'")).scalar()
            print(db, "has brain_attribution:", n)
    except Exception as e:
        print(db, "ERR", str(e)[:80])
