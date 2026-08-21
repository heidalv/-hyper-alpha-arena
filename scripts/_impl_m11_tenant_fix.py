# -*- coding: utf-8 -*-
"""M1-1 数据迁移 v2：tenant_id 修正为账户真实属主（逐表提交防中断回滚）。

依据 0004 迁移约定：tenant_id = accounts.user_id。
修正对象：所有以 account_id（或 strategy_id→ai_strategies）关联 accounts 的表。
备份已存在（bak_20260821_*），可回滚。
"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

# (表名, 关联方式)
DIRECT = [
    ("paper_positions", "account_id"),
    ("paper_orders", "account_id"),
    ("paper_balances", "account_id"),
    ("position_exit_events", "account_id"),
    ("full_auto_sessions", "account_id"),
    ("ai_strategies", "account_id"),
    ("risk_control_configs", "account_id"),
    ("signal_trade_feedback", "account_id"),
]
VIA_STRATEGY = ["strategy_trades", "strategy_memories"]

set_system_identity()
s = ScopedSession()
try:
    for t, col in DIRECT:
        try:
            n = s.execute(text(f"""
                UPDATE {t} x SET tenant_id = a.user_id
                FROM accounts a
                WHERE x.{col} = a.id AND x.tenant_id IS DISTINCT FROM a.user_id
            """)).rowcount
            s.commit()
            print(f"{t}: corrected {n} rows")
        except Exception as e:
            s.rollback()
            print(f"{t}: ERROR {str(e)[:120]}")
    for t in VIA_STRATEGY:
        try:
            n = s.execute(text(f"""
                UPDATE {t} st SET tenant_id = a.user_id
                FROM ai_strategies s JOIN accounts a ON s.account_id = a.id
                WHERE st.strategy_id = s.strategy_id
                  AND st.tenant_id IS DISTINCT FROM a.user_id
            """)).rowcount
            s.commit()
            print(f"{t}: corrected {n} rows")
        except Exception as e:
            s.rollback()
            print(f"{t}: ERROR {str(e)[:120]}")
    print("=== 残留检查（应为 0 或仅无可归属行） ===")
    for t, col in DIRECT:
        n = s.execute(text(f"SELECT count(*) FROM {t} WHERE tenant_id=1")).scalar()
        print(f"  {t} tenant=1: {n}")
    for t in VIA_STRATEGY:
        n = s.execute(text(f"SELECT count(*) FROM {t} WHERE tenant_id=1")).scalar()
        print(f"  {t} tenant=1: {n}")
    print("DONE")
finally:
    s.close()
