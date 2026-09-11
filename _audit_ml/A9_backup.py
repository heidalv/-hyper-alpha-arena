from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("before:", c.execute(text("select count(*) from brain_attribution")).scalar())
    # 备份
    c.execute(text("drop table if exists _bak_brain_attribution_20260909"))
    c.execute(text("create table _bak_brain_attribution_20260909 as select * from brain_attribution"))
    print("backup rows:", c.execute(text("select count(*) from _bak_brain_attribution_20260909")).scalar())
    c.commit()
print("backup done")
