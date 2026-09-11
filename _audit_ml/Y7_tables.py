# -*- coding: utf-8 -*-
"""查出场事件/决策落库表（用于定位「决策→成交」缺口）。"""
import os

from sqlalchemy import create_engine, text

eng = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = c.execute(text("""
        select table_name from information_schema.tables
        where table_schema='public'
          and (table_name like '%exit%' or table_name like '%position%'
               or table_name like '%decision%' or table_name like '%event%')
        order by table_name
    """)).fetchall()
    print("相关表:")
    for r in rows:
        print("  ", r[0])
