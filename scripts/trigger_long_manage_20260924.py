# -*- coding: utf-8 -*-
"""[2026-09-24] 手动触发一次长线主动管理扫描（诊断为何循环内无动作，并执行首轮管理）。

- 用真实 session（fa_7e12e7a1b6 / paper_account 14）调用 _run_midlong_active_exit。
- 该函数不使用 self，故以 None 作 self 调用。
- 打印完整异常栈（循环里是 logger.debug 吞掉的），并在之后列仓验证动作是否落地。
"""
import sys, traceback
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from dotenv import load_dotenv
load_dotenv(r"D:\001Alpha\Hyper-Alpha-Arena\.env", override=False)

from backend.database.connection import SessionLocal
from backend.database.models import FullAutoSession
from backend.services.full_auto_trading_service import FullAutoTradingService
from backend.services.paper_trading_engine import paper_engine

db = SessionLocal()
try:
    sess = db.query(FullAutoSession).filter(FullAutoSession.session_id == "fa_7e12e7a1b6").first()
    print("session:", sess.session_id, sess.status, "paper_account_id=", sess.paper_account_id)
    ms = sess.last_market_summary if isinstance(sess.last_market_summary, dict) else {}
    print("market_summary keys:", len(ms))
    print("--- 调用 _run_midlong_active_exit ---")
    try:
        FullAutoTradingService._run_midlong_active_exit(None, db, sess, ms)
        print("调用完成（无异常）")
    except Exception:
        print("!! 异常（这就是循环里被 debug 吞掉的原因）:")
        traceback.print_exc()
    db.commit()
    print("--- 动作后长线仓状态 ---")
    for p in paper_engine.get_positions(db, 14, status="open") or []:
        if str(p.get("timeframe_tier") or "").lower() != "long":
            continue
        print("  pos=%s %-6s size=%s SL=%s upnl=%s" % (
            p.get("id"), p.get("symbol"), p.get("size"), p.get("sl_price"), p.get("unrealized_pnl")))
finally:
    db.close()
