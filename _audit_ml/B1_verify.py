from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("alembic_version_core:", c.execute(text("select version_num from alembic_version_core")).scalar())
    print("rows now:", c.execute(text("select count(*) from brain_attribution")).scalar())
    print("uniq keys:", c.execute(text("select count(distinct (position_id, src, tier)) from brain_attribution")).scalar())
    print("indexes:")
    for r in c.execute(text("select indexname from pg_indexes where tablename='brain_attribution'")):
        print("  ", r[0])
    print("\n### 幂等验证：同一 key 连写 3 次")
