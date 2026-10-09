# -*- coding: utf-8 -*-
"""[2026-09-24] 诊断：长线仓 V2 管理为何没有动作 —— 直接对 4 个长线仓 dry-run decide_long。"""
import sys, traceback
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from dotenv import load_dotenv
load_dotenv(r"D:\001Alpha\Hyper-Alpha-Arena\.env", override=False)

from backend.database.connection import SessionLocal
from backend.services.paper_trading_engine import paper_engine
from backend.services.long_trend_v2 import manage_long_position, long_v2_enabled, _manage_always_enabled

db = SessionLocal()
try:
    print("long_v2_enabled() =", long_v2_enabled(), " manage_always =", _manage_always_enabled())
    positions = paper_engine.get_positions(db, 14, status="open") or []
    longs = [p for p in positions if str(p.get("timeframe_tier") or "").lower() == "long"]
    print("open positions=%d, long=%d" % (len(positions), len(longs)))
    for pos in longs:
        try:
            d = manage_long_position(db, account_id=14, position=pos)
            print("  pos=%s %-6s -> action=%s reason=%s" % (
                pos.get("id"), pos.get("symbol"), d.get("action"), str(d.get("reason"))[:90]))
            if d.get("new_sl"):
                print("        new_sl=%s ratio=%s stage=%s" % (d.get("new_sl"), d.get("ratio"), d.get("stage")))
        except Exception:
            print("  pos=%s %-6s -> EXCEPTION" % (pos.get("id"), pos.get("symbol")))
            traceback.print_exc()
finally:
    db.close()
