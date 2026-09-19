# -*- coding: utf-8 -*-
"""轮117 终检：修复后中线**是否真的开出仓**（成交 + 名义 + 拒绝原因）。"""
import io
import sys
from datetime import datetime

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import SessionLocal, AnalyticsSessionLocal

OUT = io.open("reports/_verify117_final.txt", "w", encoding="utf-8")


def w(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


db = SessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))
w("== account 14 当前 open 仓位（按开仓时间倒序）==")
for r in db.execute(text("""
    SELECT id, opened_at, symbol, timeframe_tier, trade_nature,
           COALESCE(exit_state_json::json->>'entry_source','(无)') AS src,
           ROUND((size*entry_price)::numeric,2) AS notional,
           ROUND(margin::numeric,2) AS margin
    FROM paper_positions WHERE account_id=14 AND status='open'
    ORDER BY opened_at DESC""")).fetchall():
    w("   ", tuple(r))

w("\n== 今天 16:45 之后的中线尝试结果（events）==")
adb = AnalyticsSessionLocal()
adb.execute(text("select set_config('app.is_admin','on',false)"))
for r in adb.execute(text("""
    SELECT ts, event_type, payload_json::json->>'symbol', payload_json::json->>'tier',
           LEFT(COALESCE(payload_json::json->>'reason',''),60)
    FROM mlto_thesis_events
    WHERE ts >= TIMESTAMP '2026-09-19 16:45'
      AND event_type IN ('open_execute_false','open_executed','midlong_thesis','thesis_update')
    ORDER BY ts DESC LIMIT 15""")).fetchall():
    w("   ", tuple(r))
adb.close()
db.close()
OUT.close()
print("written reports/_verify117_final.txt")
