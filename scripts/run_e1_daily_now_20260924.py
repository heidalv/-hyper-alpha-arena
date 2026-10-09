# -*- coding: utf-8 -*-
"""[2026-09-24] 手动跑一次 E1 日任务（account 14, execute=True）：验证分档止盈生效并落地首档。"""
import sys, json, traceback
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from dotenv import load_dotenv
load_dotenv(r"D:\001Alpha\Hyper-Alpha-Arena\.env", override=False)

from backend.services.trend_e1_engine import run_daily, load_last_run
from backend.database.connection import SessionLocal
from backend.services.paper_trading_engine import paper_engine

print("=== run_daily(account_ids=[14], execute=True) ===")
try:
    out = run_daily(account_ids=[14], execute=True)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:5000])
except Exception:
    traceback.print_exc()

print("\n=== 动作后长线仓 ===")
db = SessionLocal()
try:
    for p in paper_engine.get_positions(db, 14, status="open") or []:
        if str(p.get("timeframe_tier") or "").lower() != "long":
            continue
        print("  pos=%s %-6s size=%.6f SL=%s upnl=%+.2f" % (
            p.get("id"), p.get("symbol"), float(p.get("size") or 0),
            p.get("sl_price"), float(p.get("unrealized_pnl") or 0)))
finally:
    db.close()
