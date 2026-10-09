# -*- coding: utf-8 -*-
"""Open positions: peak floating pnl vs current (quantify 利润回撤), and mid recent leg details."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    print("[A] OPEN positions: peak vs now (profit pullback)")
    rows = db.execute(text(
        """SELECT id, symbol, timeframe_tier, leverage, entry_price, mark_price,
                  unrealized_pnl AS now_pnl,
                  ROUND((unrealized_pnl/NULLIF(entry_price*size,0)*100*leverage)::numeric,1) AS now_pct,
                  peak_unrealized_pnl, peak_pnl_pct,
                  sl_price, tp_price, trailing_stop_price, opened_at
           FROM paper_positions WHERE account_id=14 AND status='open'
           ORDER BY COALESCE(peak_unrealized_pnl - unrealized_pnl, 0) DESC""")).mappings().all()
    for r in rows:
        give_back = float(r["peak_unrealized_pnl"] or 0) - float(r["now_pnl"] or 0)
        print("  pos=%s %-7s %s %sx now=%+.2f (%+.1f%%) peak=%+.2f (%+.1f%%) giveback=%+.2f "
              "SL=%s TP=%s trail=%s opened=%s"
              % (r["id"], r["symbol"], r["timeframe_tier"], r["leverage"],
                 float(r["now_pnl"] or 0), float(r["now_pct"] or 0),
                 float(r["peak_unrealized_pnl"] or 0), float(r["peak_pnl_pct"] or 0),
                 give_back, r["sl_price"], r["tp_price"], r["trailing_stop_price"],
                 str(r["opened_at"])[:16]))

    print()
    print("[B] mid closed last 40: loss/wins by exit reason (recent regime)")
    rows = db.execute(text(
        """SELECT close_reason, COUNT(*) n,
                  ROUND(AVG(unrealized_pnl)::numeric,2) avg_pnl,
                  ROUND((AVG(unrealized_pnl/NULLIF(entry_price*size,0)*100*leverage))::numeric,1) avg_pct,
                  ROUND(SUM(unrealized_pnl)::numeric,2) total
           FROM paper_positions
           WHERE account_id=14 AND status IN ('closed','liquidated')
             AND COALESCE(timeframe_tier,'') ILIKE '%mid%'
             AND closed_at >= (SELECT last_reset_at FROM paper_balances WHERE account_id=14)
           GROUP BY 1 ORDER BY total""")).mappings().all()
    for r in rows:
        print("  reason=%-28s n=%3d avg_pnl=%s avg_pct%%=%s total=%s"
              % (str(r["close_reason"])[:28], r["n"], r["avg_pnl"], r["avg_pct"], r["total"]))

    print()
    print("[C] mid closed since reset: pnl% bucket distribution (small win big loss shape)")
    rows = db.execute(text(
        """SELECT CASE
                 WHEN pct <= -10 THEN '<=-10%'
                 WHEN pct <= -5  THEN '(-10,-5]'
                 WHEN pct <= 0   THEN '(-5,0]'
                 WHEN pct <= 5   THEN '(0,5]'
                 WHEN pct <= 10  THEN '(5,10]'
                 ELSE '>10%' END bucket,
               COUNT(*) n, ROUND(SUM(unrealized_pnl)::numeric,2) total
        FROM (SELECT unrealized_pnl,
                     unrealized_pnl/NULLIF(entry_price*size,0)*100*leverage AS pct
              FROM paper_positions
              WHERE account_id=14 AND status IN ('closed','liquidated')
                AND COALESCE(timeframe_tier,'') ILIKE '%mid%'
                AND closed_at >= (SELECT last_reset_at FROM paper_balances WHERE account_id=14)) t
        GROUP BY 1 ORDER BY 1""")).mappings().all()
    for r in rows:
        print("  %-10s n=%3d total=%s" % (r["bucket"], r["n"], r["total"]))
finally:
    db.close()
