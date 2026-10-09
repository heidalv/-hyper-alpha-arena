# -*- coding: utf-8 -*-
"""Inspect the big-mismatch positions: order legs vs position rows."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    ids = (4601, 4638, 4680, 4663, 4664, 4645, 4650, 4612, 4715)
    for pid in ids:
        p = db.execute(text(
            "SELECT id, symbol, opened_at, closed_at, unrealized_pnl, partial_realized_pnl, "
            "partial_fee_paid, final_fee_paid, close_reason, status, entry_price, close_price, size "
            "FROM paper_positions WHERE account_id=14 AND id=:i"), {"i": pid}).mappings().first()
        if not p:
            print(f"\npos {pid}: NOT FOUND"); continue
        print("\n" + "=" * 90)
        print(f"pos {pid} {p['symbol']} status={p['status']} reason={p['close_reason']}")
        print(f"  opened={p['opened_at']} closed={p['closed_at']}")
        print(f"  entry={p['entry_price']} close_price={p['close_price']} size={p['size']}")
        print(f"  unrealized_pnl={p['unrealized_pnl']} partial_realized={p['partial_realized_pnl']} "
              f"partial_fee={p['partial_fee_paid']} final_fee={p['final_fee_paid']}")
        ords = db.execute(text(
            "SELECT id, order_type, status, pnl, fee, created_at, filled_price, quantity, close_reason "
            "FROM paper_orders WHERE account_id=14 AND symbol=:s AND pnl IS NOT NULL "
            "AND created_at >= :lo AND created_at <= (:hi + interval '10 minutes') "
            "ORDER BY created_at"),
            {"s": p["symbol"], "lo": p["opened_at"], "hi": p["closed_at"]}).mappings().all()
        print(f"  orders with pnl in window: {len(ords)}")
        for o in ords:
            print(f"    oid={o['id']} {o['order_type']} {o['status']} pnl={round(float(o['pnl'] or 0),4)} "
                  f"fee={round(float(o['fee'] or 0),4)} qty={o['quantity']} at={o['created_at']} "
                  f"reason={o['close_reason']}")
finally:
    db.close()
