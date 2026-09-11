# -*- coding: utf-8 -*-
"""Z30a：crypto_klines 列与成交量字段探测。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("MARKET_DATABASE_URL",
                "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
eng = create_engine(URL)
with eng.connect() as c:
    cols = [r[0] for r in c.execute(text("""
        select column_name from information_schema.columns
        where table_name='crypto_klines' order by ordinal_position
    """)).fetchall()]
    print("crypto_klines 列:", ", ".join(cols))
    r = c.execute(text("""
        select * from crypto_klines where period='1h' and exchange='asterdex'
        order by timestamp desc limit 1
    """)).mappings().first()
    if r:
        for k, v in r.items():
            print(f"  {k} = {str(v)[:60]}")
