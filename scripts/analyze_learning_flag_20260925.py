# -*- coding: utf-8 -*-
"""[交易分析 R14] "学习被关掉"的策略在交易上表现如何？（目标③：学习链路 → 交易结果）

做法：paper_positions(09-15 起已平仓) ⋈ ai_strategies(strategy_id) 按 learning_enabled 分组比盈亏。
注意：策略 id 可能带后缀（如 auto_efd68a88ca），用前缀匹配连接。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

NET = """((CASE WHEN lower(p.side) IN ('long','buy') THEN (p.close_price-p.entry_price)
             ELSE (p.entry_price-p.close_price) END) * p.size)
         - coalesce(p.partial_fee_paid,0) - coalesce(p.final_fee_paid,0)"""

with SessionLocal() as s:
    print("=== learning_enabled × status 分布（ai_strategies）===")
    for x in s.execute(text("SELECT learning_enabled, status, count(*) FROM ai_strategies GROUP BY 1,2 ORDER BY 3 DESC LIMIT 12")):
        print(f"  learning_enabled={x[0]}  status={x[1]}  n={x[2]}")

    print("\n=== 目标策略明细 ===")
    for x in s.execute(text("""
        SELECT strategy_id, name, status, learning_enabled, timeframe_tier,
               take_profit_pct, stop_loss_pct, min_confidence, created_at, last_trade_at, archive_reason
        FROM ai_strategies WHERE strategy_id LIKE 'auto_efd68a88ca%' LIMIT 2
    """)):
        print(f"  id={x[0]} name={x[1]}")
        print(f"    status={x[2]} learning_enabled={x[3]} tier={x[4]} tp={x[5]} sl={x[6]} min_conf={x[7]}")
        print(f"    created={x[8]} last_trade={x[9]} archive={x[10]}")

    print("\n=== 交易按'策略是否开启学习'分组（09-15 起）===")
    for x in s.execute(text(f"""
        SELECT COALESCE(a.learning_enabled::text,'(无策略行)') le,
               count(*) n, round(sum({NET})::numeric,2) net,
               count(*) FILTER (WHERE {NET} > 0) w,
               count(DISTINCT p.strategy_id) nstrat
        FROM paper_positions p
        LEFT JOIN ai_strategies a
          ON p.strategy_id = a.strategy_id
          OR p.strategy_id = left(a.strategy_id, length(p.strategy_id))
        WHERE p.status='closed' AND p.closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 2 DESC
    """)):
        n = x[1]
        print(f"  learning_enabled={str(x[0]):12s} 交易 {n:3d} 笔 / {x[4]} 个策略  净={x[2]:>9}  胜率={x[3]/max(1,n)*100:5.1f}%")

    print("\n=== 按策略逐条（09-15 起，附带 learning_enabled）===")
    for x in s.execute(text(f"""
        SELECT p.strategy_id, COALESCE(a.learning_enabled::text,'-') le, COALESCE(a.status,'-') st,
               count(*) n, round(sum({NET})::numeric,2) net,
               count(*) FILTER (WHERE {NET} > 0) w
        FROM paper_positions p
        LEFT JOIN ai_strategies a
          ON p.strategy_id = a.strategy_id
          OR p.strategy_id = left(a.strategy_id, length(p.strategy_id))
        WHERE p.status='closed' AND p.closed_at >= '2026-09-15'
        GROUP BY 1,2,3 ORDER BY net LIMIT 10
    """)):
        print(f"  {str(x[0])[:22]:22s} learn={str(x[1]):6s} status={str(x[2])[:10]:10s} "
              f"n={x[3]:3d} 净={x[4]:>9} 胜率={x[5]/max(1,x[3])*100:5.1f}%")
