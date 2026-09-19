# -*- coding: utf-8 -*-
"""轮117 收尾：修复后中线**实际**的拒绝原因（是否还是 size_below_floor）。"""
import io
import sys
from datetime import datetime

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import AnalyticsSessionLocal

OUT = io.open("reports/_verify117_live.txt", "w", encoding="utf-8")


def w(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


adb = AnalyticsSessionLocal()
adb.execute(text("select set_config('app.is_admin','on',false)"))
w("== 本次 boot(16:2x) 之后的 open_execute_false 原因 ==")
for r in adb.execute(text("""
    SELECT ts, payload_json::json->>'symbol', payload_json::json->>'tier',
           payload_json::json->>'reason', LEFT(COALESCE(payload_json::json->>'reason_detail',''),80),
           LEFT(COALESCE(payload_json::json->>'reason_layer',''),24)
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false' AND ts >= :b
    ORDER BY ts DESC LIMIT 20"""),
    {"b": datetime(2026, 9, 19, 16, 16, 0)}).fetchall():
    w("   ", tuple(r))

w("\n== 今天 13:00 起的原因全貌（含修复前后对照）==")
for r in adb.execute(text("""
    SELECT payload_json::json->>'reason' AS why, COUNT(*), MIN(ts), MAX(ts)
    FROM mlto_thesis_events
    WHERE event_type='open_execute_false' AND ts >= TIMESTAMP '2026-09-19 13:00'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 12""")).fetchall():
    w("   ", tuple(r))
adb.close()
OUT.close()
print("written reports/_verify117_live.txt")
