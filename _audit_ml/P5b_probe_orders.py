# -*- coding: utf-8 -*-
"""P5b 真实手续费核算：paper_orders 中 scalp_mr_ 相关单的实付费用，
重算安静子集（vol_ratio<=1 且 adx_growth<=1）的真实净期望。
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

# paper_orders 结构探查
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    cols = [r[0] for r in c.execute(text("select column_name from information_schema.columns where table_name='paper_orders'")).fetchall()]
print("paper_orders 列:", cols)
