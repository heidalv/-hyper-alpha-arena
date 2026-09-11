# -*- coding: utf-8 -*-
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
    rows = c.execute(text("select table_name from information_schema.tables where table_schema='public' and table_name like '%account%' order by 1")).fetchall()
    print("account 表:", [r[0] for r in rows])
    for t in ("accounts", "trading_accounts", "paper_accounts"):
        try:
            cols = c.execute(text("select column_name from information_schema.columns where table_name=:t order by ordinal_position"), {"t": t}).fetchall()
            if cols:
                print(f"  {t} cols:", [x[0] for x in cols][:20])
        except Exception as e:
            print("  err", t, str(e)[:80]); c.rollback()
    cols = c.execute(text("select column_name from information_schema.columns where table_name='strategy_trades' order by ordinal_position")).fetchall()
    print("strategy_trades cols:", [x[0] for x in cols])
    try:
        rows = c.execute(text("select strategy_id, count(*) from strategy_trades where strategy_id like 'trend_e1%' group by 1 order by 2 desc limit 8")).fetchall()
        print("trend_e1 成交:", [tuple(x) for x in rows])
    except Exception as e:
        print("err:", str(e)[:100]); c.rollback()
    rows = c.execute(text("select timeframe_tier, count(*), min(entry_time), max(entry_time) from paper_positions group by 1 order by 2 desc")).fetchall()
    print("tier 分布:", [tuple(str(x) for x in r) for r in rows])
