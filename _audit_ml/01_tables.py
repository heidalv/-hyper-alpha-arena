import os, sys, json
from sqlalchemy import create_engine, text
os.environ.setdefault("PGCLIENTENCODING","UTF8")
url = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"
eng = create_engine(url)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = c.execute(text("""
        select table_schema, table_name from information_schema.tables
        where table_schema not in ('pg_catalog','information_schema')
        order by 1,2
    """)).fetchall()
    print("TABLES", len(rows))
    for s,t in rows:
        print(s, t)
