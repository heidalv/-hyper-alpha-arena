# -*- coding: utf-8 -*-
"""Per-position vs per-order close-leg reconciliation for account 14.

For each closed position (closed_at >= last_reset_at):
  - sum pnl & fee of filled orders (pnl not null) in [opened_at, closed_at + 10min] with same symbol
  - compare with pos.unrealized_pnl (= final + partial legs) and pos fees
Bucket into: exact match / orders_more_negative (dup suspicion) / positions_more_negative (missing legs).
"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    reset = db.execute(text(
        "SELECT last_reset_at FROM paper_balances WHERE account_id=14")).scalar()

    pos_rows = db.execute(text(
        "SELECT id, symbol, opened_at, closed_at, unrealized_pnl, partial_realized_pnl, "
        "partial_fee_paid, final_fee_paid, close_reason, status "
        "FROM paper_positions WHERE account_id=14 AND closed_at >= :r ORDER BY closed_at"),
        {"r": reset}).mappings().all()
    print("positions:", len(pos_rows), "reset:", reset)

    exact, orders_neg, pos_neg, no_orders = [], [], [], []
    for p in pos_rows:
        lo, hi = p["opened_at"], p["closed_at"]
        ords = db.execute(text(
            "SELECT id, order_type, status, pnl, fee, created_at, filled_quantity "
            "FROM paper_orders WHERE account_id=14 AND symbol=:s AND pnl IS NOT NULL "
            "AND created_at >= :lo AND created_at <= (:hi + interval '10 minutes') ORDER BY created_at"),
            {"s": p["symbol"], "lo": lo, "hi": hi}).mappings().all()
        o_pnl = sum(float(o["pnl"] or 0) for o in ords)
        o_fee = sum(float(o["fee"] or 0) for o in ords)
        pos_pnl = float(p["unrealized_pnl"] or 0)
        pos_fee = float(p["partial_fee_paid"] or 0) + float(p["final_fee_paid"] or 0)
        d = o_pnl - pos_pnl
        rec = (p["id"], p["symbol"], p["close_reason"], len(ords),
               round(o_pnl, 2), round(pos_pnl, 2), round(d, 2),
               round(o_fee, 2), round(pos_fee, 2))
        if not ords:
            no_orders.append(rec)
        elif abs(d) < 0.01:
            exact.append(rec)
        elif d < 0:
            orders_neg.append(rec)
        else:
            pos_neg.append(rec)

    def show(title, rows, limit=40):
        print(f"\n== {title}: {len(rows)} ==")
        tot = sum(r[6] for r in rows)
        print("  total delta:", round(tot, 2))
        for r in rows[:limit]:
            print("  pos=%s %s %s orders=%d o_pnl=%s pos_pnl=%s delta=%s ofee=%s pfee=%s"
                  % r)

    show("exact match", exact, 5)
    show("orders MORE negative (dup close suspicion)", orders_neg)
    show("positions MORE negative (missing order legs)", pos_neg)
    show("no close orders found", no_orders)

    # orphan orders: filled pnl orders whose symbol/time does not sit inside any closed position window
    # (window join is expensive; approximate by counting filled pnl orders not within 10min of any close)
    orphans = db.execute(text(
        """
        SELECT o.id, o.symbol, o.created_at, o.pnl, o.fee, o.order_type
        FROM paper_orders o
        WHERE o.account_id=14 AND o.pnl IS NOT NULL AND o.created_at >= :r
          AND NOT EXISTS (
            SELECT 1 FROM paper_positions pp
            WHERE pp.account_id=14 AND pp.symbol=o.symbol
              AND pp.opened_at <= o.created_at
              AND o.created_at <= (pp.closed_at + interval '10 minutes')
          )
        ORDER BY o.created_at
        """), {"r": reset}).mappings().all()
    print(f"\n== orphan pnl orders (no matching position window): {len(orphans)} ==")
    tot_o = sum(float(x["pnl"] or 0) for x in orphans)
    print("  total pnl:", round(tot_o, 2))
    for x in orphans[:40]:
        print("  id=%s %s %s pnl=%s fee=%s" % (x["id"], x["symbol"], x["created_at"],
                                               round(float(x["pnl"] or 0), 2), x["fee"]))
finally:
    db.close()
