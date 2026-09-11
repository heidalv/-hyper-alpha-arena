from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='ai_decision_logs' order by ordinal_position"))]
    print("ai_decision_logs cols:", cols)
    n=c.execute(text("select count(*) from ai_decision_logs")).scalar()
    print("n=",n)
    for r in c.execute(text("select * from ai_decision_logs order by id desc limit 3")):
        print(dict(r._mapping))
    print()
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='ai_analysis_logs' order by ordinal_position"))]
    print("ai_analysis_logs cols:", cols)
    print("n=", c.execute(text("select count(*) from ai_analysis_logs")).scalar())
    for r in c.execute(text("select * from ai_analysis_logs order by id desc limit 2")):
        d=dict(r._mapping)
        print({k:(str(v)[:200] if v is not None else None) for k,v in d.items()})
