# -*- coding: utf-8 -*-
"""Identify true duplicate full-close legs and compute the TRUE realized pnl for account 14.

Method:
  For each closed position (closed_at >= reset), find orders with pnl not null in
  [opened_at, closed_at+10min] whose qty == position final size (within 0.1%).
  - final candidate = the one created within 300s of closed_at with pnl == pos.unrealized_pnl
    (fallback: latest).
  - any OTHER such order in the window is a duplicate full-close leg -> subtract from orders sum.

Output: orders_sum, dup_sum, true_realized, positions_sum, positions+partial sum, funding, fees.
"""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    reset = db.execute(text("SELECT last_reset_at FROM paper_balances WHERE account_id=14")).scalar()

    pos_rows = db.execute(text(
        "SELECT id, symbol, opened_at, closed_at, unrealized_pnl, partial_realized_pnl, size "
        "FROM paper_positions WHERE account_id=14 AND closed_at >= :r"),
        {"r": reset}).mappings().all()

    orders_sum = 0.0
    dup_rows = []
    pos_sum = 0.0
    pos_part_sum = 0.0
    per_pos = []
    for p in pos_rows:
        size = float(p["size"] or 0)
        pos_sum += float(p["unrealized_pnl"] or 0)
        pos_part_sum += float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
        ords = db.execute(text(
            "SELECT id, order_type, pnl, created_at, filled_quantity, close_reason, fee "
            "FROM paper_orders WHERE account_id=14 AND symbol=:s AND pnl IS NOT NULL "
            "AND created_at >= :lo AND created_at <= (:hi + interval '10 minutes') "
            "ORDER BY created_at"),
            {"s": p["symbol"], "lo": p["opened_at"], "hi": p["closed_at"]}).mappings().all()
        if not ords:
            continue
        orders_sum += sum(float(o["pnl"] or 0) for o in ords)
        # full-close candidates: qty within 0.1% of final size
        fulls = [o for o in ords if size > 0 and abs(float(o["filled_quantity"] or 0) - size) <= size * 0.001]
        if len(fulls) >= 2:
            # keep the one matching pos.unrealized_pnl (closest pnl), else latest; rest are dups
            target = float(p["unrealized_pnl"] or 0)
            best = min(fulls, key=lambda o: abs(float(o["pnl"] or 0) - target))
            dups = [o for o in fulls if o["id"] != best["id"]]
            for d in dups:
                dup_rows.append(d)
                per_pos.append((p["id"], p["symbol"], d["id"], float(d["pnl"] or 0), d["created_at"],
                                d["close_reason"], best["id"], best["close_reason"]))

    dup_sum = sum(r[3] for r in per_pos)
    funding = float(db.execute(text(
        "SELECT COALESCE(SUM(payment),0) FROM paper_funding_ledger "
        "WHERE account_id=14 AND settled_at >= :r"), {"r": reset}).scalar())
    fees = float(db.execute(text(
        "SELECT COALESCE(SUM(fee),0) FROM paper_orders WHERE account_id=14 AND created_at >= :r "
        "AND fee IS NOT NULL"), {"r": reset}).scalar())

    print(f"positions closed: {len(pos_rows)}")
    print(f"orders_sum (pnl legs)      = {orders_sum:+.2f}")
    print(f"dup full-close legs: n={len(per_pos)} sum={dup_sum:+.2f}")
    print(f"TRUE realized (orders-dups)= {orders_sum - dup_sum:+.2f}")
    print(f"positions sum (unrealized) = {pos_sum:+.2f}")
    print(f"positions unreal+partial   = {pos_part_sum:+.2f}")
    print(f"funding                    = {funding:+.2f}")
    print(f"fees (orders ledger)       = {fees:+.2f}")
    print()
    print("dup leg details (pos, symbol, dup_oid, dup_pnl, at, dup_reason, kept_oid, kept_reason):")
    for r in per_pos:
        print(f"  pos={r[0]} {r[1]} dup_oid={r[2]} pnl={r[3]:+.2f} at={r[4]} reason={r[5]} "
              f"kept_oid={r[6]} kept_reason={r[7]}")
finally:
    db.close()
