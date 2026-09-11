# -*- coding: utf-8 -*-
"""模拟盘交易复盘诊断（按日期参数）。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal
from sqlalchemy import text

DAY = "2026-09-01"  # 本地时区（+08）

with system_identity(), SessionLocal() as db:
    def q(sql, params=None):
        return db.execute(text(sql), params or {}).fetchall()

    print("=== 1) 平仓单概况 ===")
    for r in q("""
        SELECT account_id, COUNT(*),
               ROUND(SUM(COALESCE(pnl,0))::numeric, 2) AS pnl_sum,
               ROUND(SUM(COALESCE(fee,0))::numeric, 2) AS fee_sum,
               SUM(CASE WHEN COALESCE(pnl,0) > 0 THEN 1 ELSE 0 END) AS wins
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY account_id ORDER BY pnl_sum
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 2) 平仓持仓概况（含分批止盈）===")
    for r in q("""
        SELECT COUNT(*) AS closed_pos,
               ROUND(SUM(COALESCE(partial_realized_pnl,0))::numeric,2) AS partial_pnl,
               ROUND(SUM(COALESCE(partial_fee_paid,0))::numeric,4) AS partial_fee
        FROM paper_positions
        WHERE closed_at >= CAST(:d AS timestamp)
          AND closed_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 3) 按 交易属性 归因 ===")
    for r in q("""
        SELECT trade_nature, COUNT(*),
               ROUND(SUM(COALESCE(pnl,0))::numeric,2) AS pnl,
               ROUND(SUM(COALESCE(fee,0))::numeric,2) AS fee,
               SUM(CASE WHEN COALESCE(pnl,0)>0 THEN 1 ELSE 0 END) AS wins
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY trade_nature ORDER BY pnl
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 4) 按 平仓原因 归因 ===")
    for r in q("""
        SELECT close_reason, COUNT(*),
               ROUND(SUM(COALESCE(pnl,0))::numeric,2) AS pnl,
               SUM(CASE WHEN COALESCE(pnl,0)>0 THEN 1 ELSE 0 END) AS wins
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY close_reason ORDER BY pnl
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 5) 按 币种 归因 ===")
    for r in q("""
        SELECT symbol, side, COUNT(*),
               ROUND(SUM(COALESCE(pnl,0))::numeric,2) AS pnl
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY symbol, side ORDER BY pnl LIMIT 18
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 6) 资金费结算 ===")
    for r in q("""
        SELECT account_id, tier, COUNT(*),
               ROUND(SUM(payment)::numeric,4) AS funding_sum
        FROM paper_funding_ledger
        WHERE settled_at >= CAST(:d AS timestamp)
          AND settled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY account_id, tier ORDER BY funding_sum
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 7) 持仓质量（MAE/峰值）===")
    for r in q("""
        SELECT COUNT(*) AS closed_pos,
               SUM(CASE WHEN trough_pnl_pct < -0.02 THEN 1 ELSE 0 END) AS mae_deep,
               ROUND(AVG(COALESCE(trough_pnl_pct,0))::numeric,4) AS avg_trough_pct,
               ROUND(AVG(COALESCE(peak_pnl_pct,0))::numeric,4) AS avg_peak_pct
        FROM paper_positions
        WHERE closed_at >= CAST(:d AS timestamp)
          AND closed_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 8) 平均持仓时长（按 tier）===")
    for r in q("""
        SELECT timeframe_tier, COUNT(*),
               ROUND(AVG(EXTRACT(EPOCH FROM (closed_at - opened_at))/3600)::numeric,2) AS avg_hours
        FROM paper_positions
        WHERE closed_at >= CAST(:d AS timestamp)
          AND closed_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY timeframe_tier ORDER BY timeframe_tier
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== 9) 最亏的 12 笔平仓单 ===")
    for r in q("""
        SELECT symbol, side, trade_nature, close_reason,
               ROUND(pnl::numeric,2) AS pnl, ROUND(fee::numeric,4) AS fee,
               ROUND(entry_price::numeric,4) AS entry, ROUND(filled_price::numeric,4) AS exit_px,
               to_char(filled_at, 'HH24:MI') AS t
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        ORDER BY pnl LIMIT 12
    """, {"d": DAY}):
        print("  ", r)

    print("\nDONE")
