from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("select * from alembic_version_core")):
        print("alembic_version_core:", dict(r._mapping))
    for r in c.execute(text("select * from alembic_version_analytics")):
        print("alembic_version_analytics:", dict(r._mapping))
