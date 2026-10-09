# -*- coding: utf-8 -*-
"""Diagnose paper account 14 balance: orders vs positions ledger, fees, funding."""
import sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))

    bal = db.execute(text(
        "SELECT account_id, initial_balance, total_equity, available_balance, "
        "realized_pnl, total_fee_paid, frozen_margin, unrealized_pnl, last_reset_at, updated_at "
        "FROM paper_balances WHERE account_id=14")).mappings().all()
    print("== balance rows ==")
    for b in bal:
        print(dict(b))

    reset = bal[0]["last_reset_at"] if bal else None
    print("\nlast_reset_at:", reset)

    o = db.execute(text(
        "SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl_sum, COALESCE(SUM(fee),0) fee_sum, "
        "COUNT(*) FILTER (WHERE pnl IS NOT NULL) n_pnl "
        "FROM paper_orders WHERE account_id=14 AND created_at >= :r"), {"r": reset}).mappings().first()
    print("\n== paper_orders since reset ==")
    print(dict(o))

    p = db.execute(text(
        "SELECT status, COUNT(*) n, COALESCE(SUM(unrealized_pnl),0) upnl_sum, "
        "COALESCE(SUM(partial_realized_pnl),0) pr_sum, COALESCE(SUM(partial_fee_paid),0) pfee_sum, "
        "COALESCE(SUM(final_fee_paid),0) ffee_sum "
        "FROM paper_positions WHERE account_id=14 AND closed_at >= :r "
        "GROUP BY status"), {"r": reset}).mappings().all()
    print("\n== paper_positions closed since reset ==")
    for r_ in p:
        print(dict(r_))

    f = db.execute(text(
        "SELECT COUNT(*) n, COALESCE(SUM(payment),0) pay_sum FROM paper_funding_ledger "
        "WHERE account_id=14 AND settled_at >= :r"), {"r": reset}).mappings().first()
    print("\n== funding since reset ==")
    print(dict(f))

    # all orders since reset with pnl, by order_type/status to see fills
    by = db.execute(text(
        "SELECT order_type, status, COUNT(*) n, COALESCE(SUM(pnl),0) pnl_sum, COALESCE(SUM(fee),0) fee_sum "
        "FROM paper_orders WHERE account_id=14 AND created_at >= :r GROUP BY 1,2 ORDER BY 3 DESC"),
        {"r": reset}).mappings().all()
    print("\n== orders by type/status ==")
    for r_ in by:
        print(dict(r_))
finally:
    db.close()
