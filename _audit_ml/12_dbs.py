from sqlalchemy import create_engine, text
for db in ["alpha_analytics","alpha_arena"]:
    try:
        eng = create_engine(f"postgresql+psycopg://laobao:alpha_pass@localhost:5432/{db}")
        with eng.connect() as c:
            c.execute(text("set app.is_admin='on'"))
            rows=c.execute(text("""select table_name from information_schema.tables where table_schema='public'
                                   and (table_name ilike '%kline%' or table_name ilike '%candle%' or table_name ilike '%bar%' or table_name ilike '%factor%')
                                   order by 1""")).fetchall()
            print("==",db,"==", [r[0] for r in rows])
            for r in rows[:20]:
                try:
                    n=c.execute(text(f'select count(*) from "{r[0]}"')).scalar()
                    print("   ",r[0],n)
                except Exception as e:
                    print("   ",r[0],"ERR",str(e)[:80])
    except Exception as e:
        print(db,"ERR",str(e)[:200])
