# -*- coding: utf-8 -*-
"""Z180（P3 前置）：确认「净口径」需要的数据在哪里 —— trade_facts / paper_orders / paper_positions。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as _t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
db.execute(_t("set app.is_admin='on'"))
try:
    for tbl in ("trade_facts", "paper_orders", "paper_positions", "paper_funding_ledger"):
        cols = db.execute(_t(
            "select column_name, data_type from information_schema.columns "
            f"where table_name='{tbl}' order by ordinal_position"
        )).fetchall()
        print(f"\n=== {tbl}（{len(cols)} 列）===")
        print("  " + ", ".join(f"{c[0]}" for c in cols))
    print("\n=== trade_facts 最近 3 行（含 fee 与否）===")
    rows = db.execute(_t(
        "select tier, pnl, * from trade_facts order by ts desc limit 3"
    )).mappings().all() if False else []
    rows = db.execute(_t("select * from trade_facts order by ts desc limit 2")).mappings().all()
    for r in rows:
        d = dict(r)
        print("  ", {k: d[k] for k in list(d)[:14]})
    print("\n=== 费用可算性：paper_orders 近 7 天 fee 合计 ===")
    r = db.execute(_t(
        "select count(*) as n, coalesce(sum(fee),0) as fee_sum from paper_orders "
        "where created_at > now() - interval '7 days'"
    )).mappings().first()
    print("  ", dict(r) if r else None)
    print("\n=== 已平仓 mid/long 持仓的毛/净对照（近 7 天）===")
    rows = db.execute(_t(
        """
        select p.tier, count(*) as n,
               sum(coalesce(p.realized_pnl,0) + coalesce(p.partial_realized_pnl,0)) as gross,
               sum(coalesce(p.total_fee_paid,0)) as fees,
               sum(coalesce(p.realized_pnl,0) + coalesce(p.partial_realized_pnl,0)
                   - coalesce(p.total_fee_paid,0)) as net
        from paper_positions p
        where p.status='closed' and p.closed_at > now() - interval '7 days'
        group by 1 order by 2 desc
        """
    )).fetchall()
    for r in rows:
        print(f"  tier={r[0]!s:6s} n={r[1]:5d} 毛={float(r[2] or 0):10.2f} 费={float(r[3] or 0):8.2f} 净={float(r[4] or 0):10.2f}")
finally:
    db.rollback()
