from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### exchange_rule_snapshots")
    print("n:", c.execute(text("select count(*) from exchange_rule_snapshots")).scalar())
    for r in c.execute(text("select * from exchange_rule_snapshots order by id desc limit 3")):
        d=dict(r._mapping)
        print({k:(str(v)[:300] if v is not None else None) for k,v in d.items()})
