from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for t in ("lane_ledger","mm_shadow_fills","mm_shadow_quotes"):
        try:
            n=c.execute(text(f'select count(*) from "{t}"')).scalar()
            print(f"== {t}: {n} 行")
            if n:
                for r in c.execute(text(f'select * from "{t}" order by 1 desc limit 3')):
                    d=dict(r._mapping)
                    print("   ", {k:(str(v)[:100] if v is not None else None) for k,v in d.items()})
        except Exception as e:
            print(f"== {t}: {str(e)[:80]}")
    print("\n### 所有含 mm 的表")
    for r in c.execute(text("""select table_name from information_schema.tables
                               where table_schema='public' and (table_name like '%mm%' or table_name like '%lane%')
                               order by 1""")):
        print("   ", r[0])
