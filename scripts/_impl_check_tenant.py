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
        SELECT table_name, column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND column_name='tenant_id'
        ORDER BY table_name""")).fetchall()
    for r in rows:
        print(r)
finally:
    s.close()
