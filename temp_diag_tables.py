# -*- coding: utf-8 -*-
"""临时诊断：列出各库表名"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from sqlalchemy import text
from backend.database.connection import engine, market_engine, analytics_engine

for name, eng in [("core(alpha_arena)", engine), ("market(alpha_market)", market_engine),
                  ("analytics(alpha_analytics)", analytics_engine)]:
    print(f"\n===== {name} =====")
    try:
        with eng.connect() as db:
            rows = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).fetchall()
            for r in rows:
                print("  ", r[0])
    except Exception as e:
        print("  ERROR:", e)
