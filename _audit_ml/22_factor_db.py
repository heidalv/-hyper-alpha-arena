from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### factor_active_set")
    for r in c.execute(text("select * from factor_active_set limit 3")):
        print(dict(r._mapping))
    print("\n### states")
    for r in c.execute(text("select state, count(*) from factor_active_set group by 1 order by 2 desc")):
        print(r)
    print("\n### factor_performance_logs cols")
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='factor_performance_logs' order by ordinal_position"))]
    print(cols)
    print("\n### recent perf sample")
    for r in c.execute(text("select * from factor_performance_logs order by 1 desc limit 3")):
        print(dict(r._mapping))
