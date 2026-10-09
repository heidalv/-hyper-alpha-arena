# -*- coding: utf-8 -*-
"""[交易分析 R2] 从 paper_positions 推导真实盈亏并做多维度分析。

盈亏口径（重要，已核对样本）：
  `partial_realized_pnl` 字段**基本为 0**（158 行里绝大多数），不可用；
  `size` 是**基础币数量**（核对：UNI size=9.4439 × entry=9.5516 = 90.2 = margin 30.07 × lev 3 ✓）。
  ⇒ pnl = (close - entry) × size × (+1 long / -1 short)；净额再减 final_fee_paid + partial_fee_paid。
进场费未单列，故 net 为"价差盈亏 − 平仓费"，略偏乐观（进场费未扣），已在结论中注明。
规矩：2026-09-15 为有效起点（此前作废）。
"""
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
    print("=== 按日（09-15 起，推导口径）===")
    print(f"  {'日期':12s} {'笔':>4s} {'净盈亏':>10s} {'胜率':>7s} {'均盈':>8s} {'均亏':>8s} {'盈亏比':>7s} {'持仓h中位':>9s}")
    for x in s.execute(text(f"""
        SELECT date_trunc('day', closed_at)::date d, count(*) n,
               sum({NET}) net,
               count(*) FILTER (WHERE {NET} > 0) w,
               avg({NET}) FILTER (WHERE {NET} > 0) aw,
               avg({NET}) FILTER (WHERE {NET} <= 0) al,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY
                   EXTRACT(EPOCH FROM (closed_at - opened_at))/3600.0) medh
        FROM paper_positions
        WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 1
    """)):
        wr = x[3] / x[1] * 100 if x[1] else 0
        pf = (abs(x[4] / x[5]) if x[4] and x[5] else None)
        print(f"  {str(x[0]):12s} {x[1]:4d} {float(x[2]):10.2f} {wr:6.1f}% "
              f"{(float(x[4]) if x[4] else 0):8.2f} {(float(x[5]) if x[5] else 0):8.2f} "
              f"{(f'{pf:.2f}' if pf else '  -  '):>7s} {float(x[6]):9.1f}")

    print("\n=== 按车道 tier（09-15 起）===")
    for x in s.execute(text(f"""
        SELECT coalesce(timeframe_tier,'?') t, count(*) n, sum({NET}) net,
               count(*) FILTER (WHERE {NET} > 0) w,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY
                   EXTRACT(EPOCH FROM (closed_at - opened_at))/3600.0) medh
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY net
    """)):
        print(f"  {x[0]:6s} n={x[1]:3d} 净={float(x[2]):9.2f} 胜率={x[3]/x[1]*100:5.1f}% 持仓中位={float(x[4]):.1f}h")

    print("\n=== 退出原因（09-15 起，按净额排序）===")
    for x in s.execute(text(f"""
        SELECT close_reason, count(*) n, sum({NET}) net,
               count(*) FILTER (WHERE {NET} > 0) w
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY net LIMIT 12
    """)):
        print(f"  {str(x[0])[:34]:34s} n={x[1]:3d} 净={float(x[2]):9.2f} 胜率={x[3]/x[1]*100:5.1f}%")

    print("\n=== 策略（09-15 起，按净额排序 Top/Bottom 各 6）===")
    rows = s.execute(text(f"""
        SELECT strategy_id, count(*) n, sum({NET}) net,
               count(*) FILTER (WHERE {NET} > 0) w
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY net
    """)).fetchall()
    for x in rows[:6] + ([("...", 0, 0, 0)] if len(rows) > 12 else []) + rows[-6:]:
        if x[0] == "...":
            print("  ...")
            continue
        print(f"  {str(x[0])[:26]:26s} n={x[1]:3d} 净={float(x[2]):9.2f} 胜率={x[3]/max(1,x[1])*100:5.1f}%")

    print("\n=== 标的（09-15 起，最亏 8 / 最赚 5）===")
    rows = s.execute(text(f"""
        SELECT symbol, count(*) n, sum({NET}) net
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY net
    """)).fetchall()
    for x in rows[:8] + rows[-5:]:
        print(f"  {x[0]:10s} n={x[1]:3d} 净={float(x[2]):9.2f}")

    print("\n=== 汇总（09-15 起 / 近 3 日 09-25~27）===")
    for label, cond in (("09-15起", "closed_at >= '2026-09-15'"),
                        ("近3日", "closed_at >= '2026-09-25'")):
        x = s.execute(text(f"""
            SELECT count(*), sum({NET}), count(*) FILTER (WHERE {NET} > 0),
                   min(closed_at), max(closed_at)
            FROM paper_positions WHERE status='closed' AND {cond}
        """)).first()
        n = x[0] or 0
        print(f"  {label}: n={n} 净={float(x[1] or 0):.2f} 胜率={x[2]/max(1,n)*100:.1f}%  窗口={x[3]} → {x[4]}")
