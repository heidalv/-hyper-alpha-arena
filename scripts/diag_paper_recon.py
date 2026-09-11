# -*- coding: utf-8 -*-
"""9/1 模拟盘完整对账：仓级全量 pnl + 开平手续费 + 资金费 + 清算。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal
from sqlalchemy import text

DAY = "2026-09-01"

with system_identity(), SessionLocal() as db:
    def q(sql, params=None):
        return db.execute(text(sql), params or {}).fetchall()

    print("=== A) 昨日平仓持仓的仓级全量已实现盈亏 ===")
    for r in q("""
        SELECT COUNT(*) AS n,
               ROUND(SUM(partial_realized_pnl
                 + (COALESCE(close_price,mark_price) - entry_price) * size
                   * CASE WHEN side='long' THEN 1 ELSE -1 END)::numeric, 2) AS full_realized,
               ROUND(SUM(partial_realized_pnl)::numeric, 2) AS partial_sum,
               ROUND(SUM((COALESCE(close_price,mark_price) - entry_price) * size
                   * CASE WHEN side='long' THEN 1 ELSE -1 END)::numeric, 2) AS final_slice
        FROM paper_positions
        WHERE closed_at >= CAST(:d AS timestamp)
          AND closed_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== B) 昨日全部成交单手续费（开仓+平仓）===")
    for r in q("""
        SELECT CASE WHEN close_reason IS NULL THEN 'open' ELSE 'close' END AS kind,
               COUNT(*), ROUND(SUM(COALESCE(fee,0))::numeric,2) AS fee
        FROM paper_orders
        WHERE status='filled'
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY 1
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== C) 昨日清算 ===")
    for r in q("""
        SELECT COUNT(*) AS n, ROUND(SUM(COALESCE(partial_realized_pnl,0))::numeric,2) AS pnl
        FROM paper_positions
        WHERE closed_at >= CAST(:d AS timestamp)
          AND closed_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
          AND close_reason ILIKE '%liquid%'
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== D) 昨日资金费 ===")
    for r in q("""
        SELECT ROUND(SUM(payment)::numeric,4) AS funding_total, COUNT(*)
        FROM paper_funding_ledger
        WHERE settled_at >= CAST(:d AS timestamp)
          AND settled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== E) 昨日全部平仓单 pnl 总和（含分批单）===")
    for r in q("""
        SELECT COUNT(*), ROUND(SUM(COALESCE(pnl,0))::numeric,2) AS pnl_sum
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
    """, {"d": DAY}):
        print("  ", r)

    print("\n=== F) 账户当前 realized_pnl（paper_balances，全期累计）===")
    for r in q("""
        SELECT account_id, ROUND(total_equity::numeric,2) AS equity,
               ROUND(realized_pnl::numeric,2) AS realized_all_time,
               ROUND(total_fee_paid::numeric,2) AS fee_all_time,
               initial_balance
        FROM paper_balances ORDER BY account_id
    """):
        print("  ", r)

    print("\n=== G) 昨日按小时平仓单 pnl 流（看亏损集中在哪个时段）===")
    for r in q("""
        SELECT to_char(filled_at, 'HH24') AS h, COUNT(*),
               ROUND(SUM(COALESCE(pnl,0))::numeric,2) AS pnl
        FROM paper_orders
        WHERE close_reason IS NOT NULL AND pnl IS NOT NULL
          AND filled_at >= CAST(:d AS timestamp)
          AND filled_at < (CAST(:d AS timestamp) + INTERVAL '1 day')
        GROUP BY 1 ORDER BY 1
    """):
        print("  ", r)

    print("\nDONE")
