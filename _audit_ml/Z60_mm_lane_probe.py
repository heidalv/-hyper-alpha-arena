# -*- coding: utf-8 -*-
"""Z60: MM 影子车道实况 + toxic_streak 死闸的真实影响面。"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text
URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = c.execute(text("select table_name from information_schema.tables where table_schema='public' and table_name like 'lane%' order by 1")).fetchall()
    print("lane 表:", [r[0] for r in rows])
    try:
        cols = c.execute(text("select column_name from information_schema.columns where table_name='trading_lanes' order by ordinal_position")).fetchall()
        print("trading_lanes cols:", [x[0] for x in cols])
        rows = c.execute(text("select id, lane_id, mode, status from trading_lanes limit 10")).fetchall() if cols else []
    except Exception as e:
        print("err:", str(e)[:100]); c.rollback()
    for t in ("trading_lanes", "lanes", "lane_registry"):
        try:
            rows = c.execute(text(f"select * from {t} limit 5")).fetchall()
            print(f"{t}:", [tuple(str(x)[:24] for x in r) for r in rows])
        except Exception as e:
            c.rollback()
    for t in ("lane_fills", "lane_ledger", "lane_shadow_report", "lane_orders"):
        try:
            n = c.execute(text(f"select count(*) from {t}")).scalar()
            print(f"  {t}: rows={n}")
        except Exception as e:
            c.rollback(); print(f"  {t}: -")
