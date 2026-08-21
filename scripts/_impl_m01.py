# -*- coding: utf-8 -*-
"""M0-1: 停会话4 + 归档147/149（重命名），全部带备份表，可回滚。"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

set_system_identity()
s = ScopedSession()
try:
    # 1. 停止会话 4（账户156"150u"的真实会话；备份表 bak_20260821_full_auto_sessions）
    r = s.execute(text(
        "UPDATE full_auto_sessions SET status='stopped', stopped_at=now() "
        "WHERE id=4 AND status='running' RETURNING id, session_id, status"
    )).fetchall()
    print("session stop:", r)

    # 2. 归档测试残留账户 147/149：重命名标注 + 确保 inactive（备份表 bak_20260821_accounts）
    r2 = s.execute(text(
        "UPDATE accounts SET name='[已归档-测试残留] ' || name, is_active='false', "
        "auto_trading_enabled='false' "
        "WHERE id IN (147,149) RETURNING id, name"
    )).fetchall()
    print("accounts archived:", r2)

    # 3. 确认账户 156 完整存在（不动）
    row = s.execute(text("SELECT id, name, account_type, is_active, initial_capital FROM accounts WHERE id=156")).fetchall()
    print("acct156 intact:", row)

    s.commit()
    print("DONE")
finally:
    s.close()
