from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("select * from alembic_version_core")):
        print("core:", dict(r._mapping))
    for r in c.execute(text("""select table_name from information_schema.tables
                               where table_name like 'alembic_version%'""")):
        print("  version tables:", r[0])
