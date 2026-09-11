from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='llm_configurations' order by ordinal_position"))]
    print("cols:", cols)
    for r in c.execute(text("select id,name,provider,model,base_url,is_active,is_default from llm_configurations order by id")):
        print(dict(r._mapping))
