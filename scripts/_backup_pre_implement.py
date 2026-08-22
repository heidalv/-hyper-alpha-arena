# -*- coding: utf-8 -*-
"""一次性安全备份：把会被本次实施修改的关键表快照到 bak_ 表（同库，RLS 绕过）。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

TABLES = [
    "accounts", "full_auto_sessions", "paper_positions", "paper_orders",
    "paper_balances", "position_exit_events", "strategy_trades",
]

set_system_identity()
s = ScopedSession()
try:
    for t in TABLES:
        bak = f"bak_20260821_{t}"
        s.execute(text(f"DROP TABLE IF EXISTS {bak}"))
        s.execute(text(f"CREATE TABLE {bak} AS SELECT * FROM {t}"))
        n = s.execute(text(f"SELECT count(*) FROM {bak}")).scalar()
        print(f"backed up {t} -> {bak}: {n} rows")
    s.commit()
    print("DONE")
finally:
    s.close()
