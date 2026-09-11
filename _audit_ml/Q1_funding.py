from sqlalchemy import create_engine, text
import statistics as st
from collections import defaultdict
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
with eng.connect() as c:
    c.execute(text("set statement_timeout='300000'"))
    cols=[r[0] for r in c.execute(text("select column_name,data_type from information_schema.columns where table_name='perp_funding' order by ordinal_position"))]
    print("perp_funding cols:", cols)
    print("rows:", c.execute(text("select count(*) from perp_funding")).scalar())
    for r in c.execute(text("select * from perp_funding order by 1 desc limit 3")):
        print(dict(r._mapping))
    print("\n### 按交易所/币种覆盖")
    for r in c.execute(text("""select count(distinct symbol) syms, count(distinct exchange) exs,
                                      min(timestamp) mn, max(timestamp) mx
                               from perp_funding""")):
        print(dict(r._mapping))
