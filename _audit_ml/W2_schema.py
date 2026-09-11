# -*- coding: utf-8 -*-
"""paper_positions schema probe."""
from sqlalchemy import create_engine, text

e = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with e.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""
        select column_name, data_type from information_schema.columns
        where table_name='paper_positions' order by ordinal_position
    """)).fetchall():
        print(r[0], r[1])
