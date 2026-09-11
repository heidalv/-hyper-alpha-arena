from sqlalchemy import create_engine, text
for db in ("alpha_analytics","alpha_arena"):
    eng = create_engine(f"postgresql+psycopg://laobao:alpha_pass@localhost:5432/{db}")
    try:
        with eng.connect() as c:
            c.execute(text("set app.is_admin='on'"))
            n=c.execute(text("select count(*) from information_schema.tables where table_name='ai_decision_logs'")).scalar()
            if not n:
                print(db, ": no ai_decision_logs"); continue
            cnt=c.execute(text("select count(*) from ai_decision_logs")).scalar()
            print(f"== {db}: {cnt} rows")
            for r in c.execute(text("select * from ai_decision_logs order by id desc limit 3")):
                d=dict(r._mapping)
                print("   ", {k:(str(v)[:120] if v is not None else None) for k,v in d.items()})
    except Exception as e:
        print(db, "ERR", str(e)[:150])
