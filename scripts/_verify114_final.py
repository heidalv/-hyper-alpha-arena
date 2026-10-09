# -*- coding: utf-8 -*-
"""轮114 最终状态：本次重启后是否还有无信息的 open_execute_false。"""
import datetime
import sys

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import AnalyticsSessionLocal

db = AnalyticsSessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))
b = datetime.datetime(2026, 9, 19, 14, 27, 19)
print("本次 boot(14:27:19) 之后：通用串/总数 =", db.execute(
    text("SELECT COUNT(*) FILTER (WHERE payload_json::json->>'reason'"
         " = 'evaluate_and_execute_returned_false'), COUNT(*) "
         "FROM mlto_thesis_events WHERE event_type='open_execute_false' AND ts >= :b"),
    {"b": b}).fetchone())
print("最近 8 条 open_execute_false：")
for r in db.execute(text(
        "SELECT ts, payload_json::json->>'symbol', payload_json::json->>'tier',"
        " payload_json::json->>'reason' FROM mlto_thesis_events"
        " WHERE event_type='open_execute_false' ORDER BY ts DESC LIMIT 8")).fetchall():
    print("   ", r)
db.close()
