# -*- coding: utf-8 -*-
"""轮114：看那条「曾经的通用串」现在的完整 payload。"""
import io
import sys

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import AnalyticsSessionLocal

OUT = io.open("reports/_verify114_live.txt", "a", encoding="utf-8")
db = AnalyticsSessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))

OUT.write("\n== 14:26:35 那条事件的完整 payload（同日同类事件此前只有通用串 + 空 detail）==\n")
for r in db.execute(text("""
    SELECT ts, payload_json
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false'
      AND payload_json::json->>'reason' LIKE 'eval_false:cooldown:%'
    ORDER BY ts DESC LIMIT 3""")).fetchall():
    OUT.write(f"   {r[0]}\n   {r[1]}\n")

OUT.write("\n== 对照：修复前最后一条通用串（09-19 14:10:00）==\n")
for r in db.execute(text("""
    SELECT ts, payload_json
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false'
      AND payload_json::json->>'reason' = 'evaluate_and_execute_returned_false'
    ORDER BY ts DESC LIMIT 1""")).fetchall():
    OUT.write(f"   {r[0]}\n   {r[1]}\n")

db.close()
OUT.close()
print("appended")
