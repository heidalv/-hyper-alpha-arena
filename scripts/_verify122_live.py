# -*- coding: utf-8 -*-
"""轮122 收尾：AI 中线池是否已按置信度对齐看板 + ZEC 是否已根除。"""
import io
import json
import sys

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import SessionLocal

OUT = io.open("reports/_verify122_live.txt", "w", encoding="utf-8")
SID = "fa_7e12e7a1b6"


def w(*a):
    OUT.write(" ".join(str(x) for x in a) + "\n")


db = SessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))

w("== 会话 AI 中线池（落盘）==")
try:
    d = json.loads(io.open(f"backend/services/data/ai_coin_unified/{SID}.json",
                           encoding="utf-8").read())
    w("   mid  =", d.get("mid"))
    w("   long =", d.get("long"))
except Exception as e:
    w("   读取失败", type(e).__name__, str(e)[:120])

w("\n== 函数返回 vs 看板最新一轮 ==")
try:
    from backend.services.auto_coin_selector import get_ai_mid_candidates_for_session as G
    w("   get_ai_mid_candidates_for_session =", G(SID, db=db))
except Exception as e:
    w("   失败", type(e).__name__, str(e)[:160])

w("   看板最新一轮（horizon=mid）:")
for r in db.execute(text("""
    SELECT symbol, confidence, ai_verdict FROM coin_select_candidates
    WHERE horizon='mid' AND created_at = (SELECT MAX(created_at) FROM coin_select_candidates
                                          WHERE horizon='mid')
    ORDER BY confidence DESC""")).fetchall():
    w("      ", tuple(r))

w("\n== ZEC 现状 ==")
for r in db.execute(text("""
    SELECT strategy_id, status, timeframe_tier FROM ai_strategies
    WHERE primary_symbol='ZEC'""")).fetchall():
    w("   ", tuple(r))
w("   近 30 分钟 ZEC 相关事件:")
for r in db.execute(text("""
    SELECT ts, event_type, LEFT(COALESCE(payload_json::json->>'reason',''),50)
    FROM mlto_thesis_events WHERE ts >= now() - interval '30 minutes'
      AND payload_json::json->>'symbol'='ZEC' ORDER BY ts DESC LIMIT 5""")).fetchall():
    w("      ", tuple(r))

db.close()
OUT.close()
print("written reports/_verify122_live.txt")
