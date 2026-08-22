# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

set_system_identity()
s = ScopedSession()
try:
    print("=== tenant=1 paper_positions 明细 ===")
    for r in s.execute(text("""
        SELECT id, account_id, symbol, trade_nature, status, opened_at, updated_at, strategy_id
        FROM paper_positions WHERE tenant_id=1 ORDER BY id""")).fetchall():
        print("  ", r)
    print("=== closed<opened strategy_trades 明细 ===")
    for r in s.execute(text("""
        SELECT id, strategy_id, symbol, opened_at, closed_at, pnl
        FROM strategy_trades WHERE closed_at IS NOT NULL AND closed_at < opened_at
        ORDER BY id DESC LIMIT 20""")).fetchall():
        print("  ", r)
finally:
    s.close()
