# -*- coding: utf-8 -*-
"""[交易分析 R3] 单笔明细：最亏/最赚、盈亏比、账户分布、近 3 日逐笔。"""
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

with SessionLocal() as s:
    print("=== 账户分布（09-15 起已平仓）===")
    for x in s.execute(text(f"""
        SELECT account_id, count(*), round(sum({NET})::numeric,2),
               count(*) FILTER (WHERE {NET} > 0)
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 2 DESC
    """)):
        print(f"  acct={x[0]} n={x[1]:3d} 净={x[2]} 胜={x[3]}")

    print("\n=== 盈亏比与均值（09-15 起，按车道）===")
    for x in s.execute(text(f"""
        SELECT coalesce(timeframe_tier,'?') t, count(*) n,
               avg({NET}) FILTER (WHERE {NET} > 0) aw,
               avg({NET}) FILTER (WHERE {NET} <= 0) al,
               count(*) FILTER (WHERE {NET} > 0) w,
               round(sum(coalesce(final_fee_paid,0)+coalesce(partial_fee_paid,0))::numeric,2) fees
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1
    """)):
        aw = float(x[2] or 0); al = float(x[3] or 0)
        print(f"  {x[0]:6s} n={x[1]:3d} 均盈={aw:6.2f} 均亏={al:7.2f} 盈亏比={(abs(aw/al) if al else 0):5.2f} 胜率={x[4]/x[1]*100:5.1f}% 手续费合计={x[5]}")

    print("\n=== 最亏 10 笔 ===")
    for x in s.execute(text(f"""
        SELECT symbol, timeframe_tier, strategy_id, side, entry_price, close_price,
               size, margin, {NET} net, opened_at, closed_at, close_reason
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        ORDER BY {NET} ASC LIMIT 10
    """)):
        notional = float(x[6]) * float(x[4])
        pct = float(x[8]) / float(x[7]) * 100 if x[7] else 0
        hrs = (x[10] - x[9]).total_seconds() / 3600 if x[9] and x[10] else 0
        print(f"  {x[0]:8s} {str(x[1]):5s} {str(x[2])[:16]:16s} {x[3]:5s} 净={float(x[8]):8.2f} "
              f"({pct:6.1f}%保证金) 名义={notional:8.1f} 持仓={hrs:5.1f}h {str(x[11])[:22]}")

    print("\n=== 最赚 6 笔 ===")
    for x in s.execute(text(f"""
        SELECT symbol, timeframe_tier, strategy_id, side, entry_price, close_price,
               size, margin, {NET} net, opened_at, closed_at, close_reason
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        ORDER BY {NET} DESC LIMIT 6
    """)):
        notional = float(x[6]) * float(x[4])
        pct = float(x[8]) / float(x[7]) * 100 if x[7] else 0
        hrs = (x[10] - x[9]).total_seconds() / 3600 if x[9] and x[10] else 0
        print(f"  {x[0]:8s} {str(x[1]):5s} {str(x[2])[:16]:16s} {x[3]:5s} 净={float(x[8]):8.2f} "
              f"({pct:6.1f}%保证金) 名义={notional:8.1f} 持仓={hrs:5.1f}h {str(x[11])[:22]}")

    print("\n=== 近 3 日（09-25 起）逐笔 ===")
    for x in s.execute(text(f"""
        SELECT closed_at, symbol, timeframe_tier, side, {NET} net, margin, close_reason
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-25'
        ORDER BY closed_at
    """)):
        pct = float(x[4]) / float(x[5]) * 100 if x[5] else 0
        print(f"  {str(x[0])[5:16]} {x[1]:8s} {str(x[2]):5s} {x[3]:5s} 净={float(x[4]):7.2f} ({pct:6.1f}%) {str(x[6])[:24]}")
