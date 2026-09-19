# -*- coding: utf-8 -*-
"""轮122 终检：ZEC/SYN/WLFI/ICP 的论题状态 + 池子何时换血。"""
import io
import sys

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import AnalyticsSessionLocal, SessionLocal

OUT = io.open("reports/_verify122_live.txt", "a", encoding="utf-8")


def w(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


a = AnalyticsSessionLocal()
a.execute(text("select set_config('app.is_admin','on',false)"))
w("\n== 论题状态（AI 池币 vs 看板 approve 的 ICP）==")
for r in a.execute(text("""
    SELECT symbol, accepted, recommend_open, updated_at, expires_at
    FROM mlto_thesis WHERE tier='mid' AND session_id='fa_7e12e7a1b6'
      AND symbol IN ('ZEC','SYN','WLFI','ICP') ORDER BY symbol""")).fetchall():
    w("   ", tuple(r))
a.close()

db = SessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))
w("\n== 看板扫描表（换代时间）==")
try:
    for r in db.execute(text("""
        SELECT status, MAX(finished_at), COUNT(*)
        FROM coin_select_scans GROUP BY 1""")).fetchall():
        w("   ", tuple(r))
except Exception as e:
    w("   (失败)", type(e).__name__, str(e)[:120])

w("\n== 当前 mid 持仓（本轮的试探仓）==")
for r in db.execute(text("""
    SELECT id, symbol, timeframe_tier, ROUND((size*entry_price)::numeric,2),
           ROUND(unrealized_pnl::numeric,2), opened_at
    FROM paper_positions WHERE account_id=14 AND status='open' AND timeframe_tier='mid'
    ORDER BY opened_at DESC""")).fetchall():
    w("   ", tuple(r))
db.close()
OUT.close()
print("written reports/_verify122_live.txt")
