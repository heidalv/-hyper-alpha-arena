from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""select id, symbol, decision_time, decision_source, operation
                               from ai_decision_logs order by id desc limit 8""")):
        print(dict(r._mapping))
    print("\n### 各 decision_source 计数（近 1 小时）")
    for r in c.execute(text("""select decision_source, count(*) n, max(decision_time) mx
                               from ai_decision_logs where decision_time > now() - interval '1 hour'
                               group by 1 order by n desc""")):
        print(dict(r._mapping))
