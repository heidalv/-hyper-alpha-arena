# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

set_system_identity()
s = ScopedSession()
try:
    rows = s.execute(text("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND (
          table_name ILIKE '%flow%' OR table_name ILIKE '%orderflow%'
          OR table_name ILIKE '%cvd%' OR table_name ILIKE '%tick%' OR table_name ILIKE '%trade%'
          OR table_name ILIKE '%micro%' OR table_name ILIKE '%depth%')
        ORDER BY 1""")).fetchall()
    print("flow-related tables:", [r[0] for r in rows])
    rows2 = s.execute(text("SELECT exchange, period, count(*), min(timestamp), max(timestamp) FROM crypto_klines WHERE symbol='BTC' AND period='15m' GROUP BY 1,2 LIMIT 5")).fetchall()
    print("BTC 15m availability:", rows2)
    rows3 = s.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name ILIKE '%kline%'")).fetchall()
    print("kline tables:", [r[0] for r in rows3])
finally:
    s.close()
