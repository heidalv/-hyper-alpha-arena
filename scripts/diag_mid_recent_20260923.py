# -*- coding: utf-8 -*-
"""Mid-tier recent performance check: quantify small-win big-loss, open exposure, exit params."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))

    print("=" * 100)
    print("[1] OPEN positions (floating pnl, sorted asc)")
    rows = db.execute(text(
        """SELECT id, symbol, side, timeframe_tier, leverage, entry_price, mark_price, size,
                  unrealized_pnl,
                  ROUND((unrealized_pnl/NULLIF(entry_price*size,0)*100*leverage)::numeric,1) AS pnl_pct,
                  sl_price, tp_price, trailing_stop_price,
                  (now() - opened_at) AS hold
           FROM paper_positions WHERE account_id=14 AND status='open' ORDER BY unrealized_pnl""")).mappings().all()
    for r in rows:
        print("  pos=%s %-7s tier=%s %s %sx entry=%s mark=%s upnl=%+.2f pnl%%=%+.1f%% SL=%s TP=%s trail=%s hold=%s"
              % (r["id"], r["symbol"], r["timeframe_tier"], r["side"], r["leverage"],
                 r["entry_price"], r["mark_price"], float(r["unrealized_pnl"] or 0),
                 float(r["pnl_pct"] or 0), r["sl_price"], r["tp_price"],
                 r["trailing_stop_price"], r["hold"]))

    print("=" * 100)
    print("[2] closed positions per tier since reset: win/loss asymmetry")
    rows = db.execute(text(
        """SELECT COALESCE(timeframe_tier,'?') tier, COUNT(*) n,
                  SUM(CASE WHEN unrealized_pnl>0 THEN 1 ELSE 0 END) wins,
                  ROUND(AVG(CASE WHEN unrealized_pnl>0 THEN unrealized_pnl END)::numeric,2) avg_win,
                  ROUND(AVG(CASE WHEN unrealized_pnl<=0 THEN unrealized_pnl END)::numeric,2) avg_loss,
                  ROUND(SUM(unrealized_pnl)::numeric,2) total,
                  ROUND((SUM(CASE WHEN unrealized_pnl>0 THEN unrealized_pnl END)
                         / NULLIF(ABS(SUM(CASE WHEN unrealized_pnl<=0 THEN unrealized_pnl END)),0))::numeric,2) pf
           FROM paper_positions
           WHERE account_id=14 AND status IN ('closed','liquidated')
             AND closed_at >= (SELECT last_reset_at FROM paper_balances WHERE account_id=14)
           GROUP BY 1 ORDER BY total""")).mappings().all()
    for r in rows:
        wr = (100.0 * r["wins"] / r["n"]) if r["n"] else 0.0
        print("  tier=%-8s n=%3d winrate=%4.1f%% avg_win=%s avg_loss=%s PF=%s total=%s"
              % (r["tier"], r["n"], wr, r["avg_win"], r["avg_loss"], r["pf"], r["total"]))

    print("=" * 100)
    print("[3] mid tier: last 40 closed positions (newest first)")
    rows = db.execute(text(
        """SELECT id, symbol, side, leverage, entry_price, close_price, size, unrealized_pnl,
                  ROUND((unrealized_pnl/NULLIF(entry_price*size,0)*100*leverage)::numeric,1) AS pnl_pct,
                  close_reason, opened_at, closed_at,
                  ROUND(EXTRACT(EPOCH FROM (closed_at-opened_at))/3600.0,2) hold_h,
                  sl_price, tp_price, trailing_stop_price, peak_pnl_pct
           FROM paper_positions
           WHERE account_id=14 AND status IN ('closed','liquidated')
             AND COALESCE(timeframe_tier,'') ILIKE '%mid%'
           ORDER BY closed_at DESC LIMIT 40""")).mappings().all()
    for r in rows:
        print("  pos=%s %-7s %sx pnl=%+7.2f (%+6.1f%%) peak%%=%+6.1f hold=%sh SL=%s TP=%s trail=%s closed=%s reason=%s"
              % (r["id"], r["symbol"], r["leverage"], float(r["unrealized_pnl"] or 0),
                 float(r["pnl_pct"] or 0), float(r["peak_pnl_pct"] or 0), r["hold_h"],
                 r["sl_price"], r["tp_price"], r["trailing_stop_price"],
                 str(r["closed_at"])[:16], str(r["close_reason"])[:42]))

    print("=" * 100)
    print("[4] mid/exit related config keys")
    rows = db.execute(text(
        """SELECT key, value FROM app_config WHERE key ILIKE '%mid%' OR key ILIKE '%exit%'
           ORDER BY key""")).mappings().all()
    for r in rows:
        print("  %s = %s" % (r["key"], str(r["value"])[:120]))
finally:
    db.close()
