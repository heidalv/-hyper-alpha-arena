# -*- coding: utf-8 -*-
"""[交易分析 R5] 定位 <1h "秒止损" 的构成：按日/标的/策略/方向/退出原因。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

PNL = """
  (CASE WHEN lower(side) IN ('long','buy') THEN (close_price - entry_price)
        ELSE (entry_price - close_price) END) * size
"""
NET = f"(({PNL}) - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0))"
HOLD_H = "EXTRACT(EPOCH FROM (closed_at - opened_at))/3600.0"
W = f"status='closed' AND closed_at >= '2026-09-15' AND {HOLD_H} < 1"

with SessionLocal() as s:
    print("=== <1h 秒止损：按日 ===")
    for x in s.execute(text(f"""
        SELECT date_trunc('day', opened_at)::date d, count(*) n,
               round(sum({NET})::numeric,2) net
        FROM paper_positions WHERE {W} GROUP BY 1 ORDER BY 1
    """)):
        print(f"  {x[0]}  n={x[1]:2d}  净={x[2]}")

    print("\n=== <1h：按标的 ===")
    for x in s.execute(text(f"""
        SELECT symbol, count(*) n, round(sum({NET})::numeric,2) net
        FROM paper_positions WHERE {W} GROUP BY 1 ORDER BY net LIMIT 10
    """)):
        print(f"  {x[0]:10s} n={x[1]:2d} 净={x[2]}")

    print("\n=== <1h：按策略 ===")
    for x in s.execute(text(f"""
        SELECT strategy_id, count(*) n, round(sum({NET})::numeric,2) net
        FROM paper_positions WHERE {W} GROUP BY 1 ORDER BY net LIMIT 8
    """)):
        print(f"  {str(x[0])[:26]:26s} n={x[1]:2d} 净={x[2]}")

    print("\n=== <1h：按方向/退出原因 ===")
    for x in s.execute(text(f"""
        SELECT side, close_reason, count(*) n, round(sum({NET})::numeric,2) net
        FROM paper_positions WHERE {W} GROUP BY 1,2 ORDER BY net LIMIT 8
    """)):
        print(f"  {str(x[0]):5s} {str(x[1])[:26]:26s} n={x[2]:2d} 净={x[3]}")

    print("\n=== <1h：逐笔明细（含入场/平仓价、SL 距离）===")
    for x in s.execute(text(f"""
        SELECT opened_at, closed_at, symbol, timeframe_tier, side, entry_price, close_price,
               sl_price, {NET} net, margin, close_reason
        FROM paper_positions WHERE {W} ORDER BY net LIMIT 12
    """)):
        hrs = (x[1] - x[0]).total_seconds() / 3600
        slip = ""
        try:
            if x[7]:
                slip = f" SL={float(x[7]):.6g}"
        except Exception:
            pass
        print(f"  {str(x[0])[5:16]} {x[2]:9s} {str(x[3]):5s} {x[4]:5s} "
              f"in={float(x[5]):.6g} out={float(x[6]):.6g}{slip} 净={float(x[8]):7.2f} {hrs:4.2f}h {str(x[10])[:16]}")
