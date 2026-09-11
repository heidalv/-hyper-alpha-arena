# -*- coding: utf-8 -*-
"""重启后验证：新代码不再产生 CVD 假大单，聚合鲸鱼新行语义正确。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from sqlalchemy import create_engine, text

e = create_engine(
    "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market",
    isolation_level="AUTOCOMMIT",
)
with e.connect() as db:
    print("近 12 分钟 whale_activities 新增（按 activity_type×blockchain）:")
    for r in db.execute(text(
        "SELECT activity_type, blockchain, COUNT(*) FROM whale_activities "
        "WHERE created_at > NOW() - INTERVAL '12 minutes' "
        "GROUP BY activity_type, blockchain ORDER BY 3 DESC"
    )):
        print("  ", r)
    print("aggregate_whale 最新 8 行（新采集器：去重+分档阈值）:")
    for r in db.execute(text(
        "SELECT symbol, direction, amount_usd, signal_direction, timestamp "
        "FROM whale_activities WHERE activity_type='aggregate_whale' "
        "ORDER BY timestamp DESC LIMIT 8"
    )):
        print("  ", r)
    fake = db.execute(text(
        "SELECT COUNT(*) FROM whale_activities WHERE activity_type='large_order' "
        "AND blockchain='exchange'"
    )).scalar()
    print("CVD 假大单存量:", fake)
    assert fake == 0, "仍有 CVD 假大单!"
print("OK")
