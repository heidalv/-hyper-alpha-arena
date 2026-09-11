from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### 含 mm / lane 的表")
    for r in c.execute(text("""select table_name from information_schema.tables
                               where table_schema='public' and (table_name like '%mm%' or table_name like '%lane%')
                               order by 1""")):
        print("   ", r[0])
    print("\n### lane_ledger 内容")
    try:
        n=c.execute(text("select count(*) from lane_ledger")).scalar()
        print("  n =", n)
        for r in c.execute(text("select * from lane_ledger order by id desc limit 5")):
            d=dict(r._mapping)
            print("   ", {k:(str(v)[:90] if v is not None else None) for k,v in d.items()})
    except Exception as e:
        print("  ERR", str(e)[:120])
