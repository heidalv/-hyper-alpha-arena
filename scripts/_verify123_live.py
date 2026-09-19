# -*- coding: utf-8 -*-
"""轮123 收尾证据：AI 中线池是否随看板换代 + ZEC 是否绝迹。"""
import io
import json
import sys

sys.path.insert(0, ".")
from sqlalchemy import text
from backend.database.connection import SessionLocal

OUT = io.open("reports/_verify123_live.txt", "w", encoding="utf-8")
SID = "fa_7e12e7a1b6"
db = SessionLocal()
db.execute(text("select set_config('app.is_admin','on',false)"))

OUT.write("== 会话 AI 中线池（落盘）==\n")
d = json.loads(io.open(f"backend/services/data/ai_coin_unified/{SID}.json",
                       encoding="utf-8").read())
OUT.write(f"   mid = {d.get('mid')}\n")

OUT.write("\n== 看板最新一轮（horizon=mid）==\n")
for r in db.execute(text("""
    SELECT symbol, confidence, ai_verdict FROM coin_select_candidates
    WHERE horizon='mid' AND created_at=(SELECT MAX(created_at) FROM coin_select_candidates
                                        WHERE horizon='mid')
    ORDER BY confidence DESC""")).fetchall():
    OUT.write(f"   {tuple(r)}\n")

OUT.write("\n== 看板换代时间（新判据：候选表 MAX(created_at)）==\n")
OUT.write("   " + str(db.execute(text(
    "SELECT MAX(created_at) FROM coin_select_candidates WHERE horizon='mid'")).scalar()) + "\n")
OUT.write("   （旧判据 coin_select_scans.finished_at 停在 15:52 —— 本轮已弃用）\n")

OUT.write("\n== ZEC ==\n")
for r in db.execute(text(
        "SELECT strategy_id, status, timeframe_tier FROM ai_strategies "
        "WHERE primary_symbol='ZEC'")).fetchall():
    OUT.write(f"   {tuple(r)}\n")
OUT.write("   池内是否仍含 ZEC: " + str("ZEC" in (d.get("mid") or {}).get("symbols", [])) + "\n")
db.close()
OUT.close()
print("written reports/_verify123_live.txt")
