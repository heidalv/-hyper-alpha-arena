# -*- coding: utf-8 -*-
"""[交易分析 R4] churn（开仓即止损）、VIRTUAL 连环亏损、符号封禁配置核查。"""
from __future__ import annotations

import os
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

with SessionLocal() as s:
    print("=== churn：持仓时长分桶（09-15 起）===")
    for x in s.execute(text(f"""
        SELECT CASE WHEN {HOLD_H} < 1 THEN '<1h'
                    WHEN {HOLD_H} < 6 THEN '1-6h'
                    WHEN {HOLD_H} < 24 THEN '6-24h'
                    ELSE '>=24h' END b,
               count(*) n, round(sum({NET})::numeric,2) net,
               count(*) FILTER (WHERE {NET} > 0) w,
               round(avg({NET})::numeric,2) avg
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY min({HOLD_H})
    """)):
        print(f"  {x[0]:6s} n={x[1]:3d} 净={x[2]:>9} 均={x[4]:>7} 胜率={x[3]/x[1]*100:5.1f}%")

    print("\n=== VIRTUAL 全部平仓（09-15 起）===")
    for x in s.execute(text(f"""
        SELECT closed_at, timeframe_tier, {NET} net, margin, close_reason, {HOLD_H}
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
          AND symbol='VIRTUAL' ORDER BY closed_at
    """)):
        pct = float(x[2]) / float(x[3]) * 100 if x[3] else 0
        print(f"  {str(x[0])[5:16]} {str(x[1]):5s} 净={float(x[2]):7.2f} ({pct:6.1f}%) 持仓={float(x[5]):5.1f}h {str(x[4])[:20]}")

    print("\n=== 每个标的小计（09-15 起，按净额）===")
    for x in s.execute(text(f"""
        SELECT symbol, count(*) n, round(sum({NET})::numeric,2) net,
               count(*) FILTER (WHERE {NET} > 0) w
        FROM paper_positions WHERE status='closed' AND closed_at >= '2026-09-15'
        GROUP BY 1 HAVING count(*) >= 3 ORDER BY net LIMIT 10
    """)):
        print(f"  {x[0]:10s} n={x[1]:3d} 净={x[2]:>9} 胜率={x[3]/x[1]*100:5.1f}%")

print("\n=== 符号封禁/风控相关配置 ===")
for k in ("SYMBOL_RISK_BAN_HOURS", "MIDLONG_MAX_OPEN_POSITIONS", "PC_MAX_WEIGHT_PER_SYMBOL_MID",
          "MIDLONG_SL_MAX_PCT_MID", "MIDLONG_SL_MAX_PCT_LONG", "MIDLONG_BELIEF_LOOP_ENABLED",
          "V3_FACTOR_MAX_SECONDS", "MLTO_OWM_INTO_BRAIN_MODE"):
    print(f"  {k} = {os.getenv(k, '(未设置/.env 未加载)')}")
