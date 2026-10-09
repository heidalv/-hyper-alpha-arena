# -*- coding: utf-8 -*-
"""[交易分析 R13] 补齐目标①的缺口：分策略盈亏比/利润因子、按退出原因的持仓与峰值利用。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

NET = """((CASE WHEN lower(side) IN ('long','buy') THEN (close_price-entry_price)
             ELSE (entry_price-close_price) END) * size)
         - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0)"""
HOLD = "EXTRACT(EPOCH FROM (closed_at - opened_at))/3600.0"

with SessionLocal() as s:
    print("=== 分策略：笔数/净/胜率/盈亏比/利润因子（09-15 起，n≥3）===")
    for x in s.execute(text(f"""
        SELECT strategy_id, count(*) n, sum({NET}) net,
               count(*) FILTER (WHERE {NET} > 0) w,
               sum({NET}) FILTER (WHERE {NET} > 0) gp,
               sum({NET}) FILTER (WHERE {NET} <= 0) gl,
               avg({NET}) FILTER (WHERE {NET} > 0) aw,
               avg({NET}) FILTER (WHERE {NET} <= 0) al
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 HAVING count(*) >= 3 ORDER BY net
    """)):
        aw, al = float(x[6] or 0), float(x[7] or 0)
        pf = (float(x[4]) / abs(float(x[5]))) if x[5] else float("inf")
        print(f"  {str(x[0])[:24]:24s} n={x[1]:3d} 净={float(x[2]):8.2f} 胜率={x[3]/x[1]*100:5.1f}% "
              f"盈亏比={(abs(aw/al) if al else 0):5.2f} 利润因子={pf:5.2f}")

    print("\n=== 按退出原因：笔数/净/胜率/持仓中位/峰值利用（peak_pnl_pct 中位）===")
    for x in s.execute(text(f"""
        SELECT close_reason, count(*) n, sum({NET}) net,
               count(*) FILTER (WHERE {NET} > 0) w,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY {HOLD}) medh,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY peak_pnl_pct) medpeak,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY trough_pnl_pct) medtrough
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 HAVING count(*) >= 2 ORDER BY net LIMIT 10
    """)):
        print(f"  {str(x[0])[:30]:30s} n={x[1]:3d} 净={float(x[2]):8.2f} 胜率={x[3]/x[1]*100:5.1f}% "
              f"持仓中位={float(x[4]):5.1f}h 峰值中位={float(x[5] or 0):6.2f}% 谷值中位={float(x[6] or 0):6.2f}%")

    print("\n=== 分车道：利润因子 ===")
    for x in s.execute(text(f"""
        SELECT timeframe_tier, count(*) n, sum({NET}) net,
               sum({NET}) FILTER (WHERE {NET} > 0) gp,
               sum({NET}) FILTER (WHERE {NET} <= 0) gl
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1
    """)):
        pf = (float(x[3]) / abs(float(x[4]))) if x[4] else float("inf")
        print(f"  {str(x[0]):6s} n={x[1]:3d} 净={float(x[2]):8.2f} 利润因子={pf:5.2f}")
