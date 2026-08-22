# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

set_system_identity()
s = ScopedSession()
try:
    n1 = s.execute(text(
        "UPDATE paper_positions x SET tenant_id = a.user_id FROM accounts a "
        "WHERE x.account_id = a.id AND x.tenant_id IS DISTINCT FROM a.user_id"
    )).rowcount
    s.commit()
    print("tenant fix rows:", n1)
    n2 = s.execute(text(
        "UPDATE strategy_trades SET opened_at = opened_at - interval '8 hours' "
        "WHERE closed_at IS NOT NULL AND closed_at < opened_at"
    )).rowcount
    s.commit()
    print("tz fix rows:", n2)
    r1 = s.execute(text("SELECT count(*) FROM paper_positions WHERE tenant_id=1")).scalar()
    r2 = s.execute(text(
        "SELECT count(*) FROM strategy_trades WHERE closed_at IS NOT NULL AND closed_at < opened_at"
    )).scalar()
    print("residual tenant=1:", r1, "| residual closed<opened:", r2)
finally:
    s.close()
