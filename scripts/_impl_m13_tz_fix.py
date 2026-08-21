# -*- coding: utf-8 -*-
"""M1-3 历史数据修复：strategy_trades 时区混写。
opened_at 曾按北京钟面（+8）写入、closed_at 按 naive UTC 写入 → closed<opened。
把 opened_at 归一到 UTC 钟面（-8h）。仅修正 closed_at < opened_at 的行。
备份：bak_20260821_strategy_trades。
"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

set_system_identity()
s = ScopedSession()
try:
    r = s.execute(text("""
        UPDATE strategy_trades
        SET opened_at = opened_at - interval '8 hours'
        WHERE closed_at IS NOT NULL AND closed_at < opened_at
        RETURNING id
    """))
    ids = [x[0] for x in r.fetchall()]
    s.commit()
    print(f"fixed {len(ids)} rows: {ids[:10]}...")
    # 校验
    bad = s.execute(text("""
        SELECT count(*) FROM strategy_trades
        WHERE closed_at IS NOT NULL AND closed_at < opened_at
    """)).scalar()
    print("remaining closed<opened:", bad)
    print("DONE")
finally:
    s.close()
