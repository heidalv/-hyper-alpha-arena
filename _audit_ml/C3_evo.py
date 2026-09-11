from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### factor_evolution_log cols")
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='factor_evolution_log' order by ordinal_position"))]
    print(cols)
    print("count:", c.execute(text("select count(*) from factor_evolution_log")).scalar())
    print("\n### 最近 5 条")
    for r in c.execute(text("select * from factor_evolution_log order by id desc limit 5")):
        d=dict(r._mapping)
        print({k:(str(v)[:180] if v is not None else None) for k,v in d.items()})
    print("\n### factor_quality_reports cols")
    cols=[r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='factor_quality_reports' order by ordinal_position"))]
    print(cols)
    for r in c.execute(text("select * from factor_quality_reports order by id desc limit 3")):
        d=dict(r._mapping)
        print({k:(str(v)[:200] if v is not None else None) for k,v in d.items()})
