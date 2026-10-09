# -*- coding: utf-8 -*-
"""[学习闭环 R2] 用**精确等值连接**重算分组（更正 §97 的前缀模糊连接），并列出被 Gate A 挡掉的策略。

背景：§97 我用了 `p.strategy_id = a.strategy_id OR p.strategy_id = left(a.strategy_id, length(p.strategy_id))`
这类模糊连接 ⇒ 可能把 A 策略的行匹配到 B 策略上。而运行时门控 `_is_learning_enabled` 用的是**精确等值**。
本脚本以**精确等值**为准，并用日志实证（35 条 Gate A 命中）交叉验证。
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
    print("=== 精确等值连接：按 learning_enabled 分组（09-15 起）===")
    for x in s.execute(text(f"""
        SELECT COALESCE(a.learning_enabled::text,'(无精确行)') le,
               count(*) n, count(DISTINCT p.strategy_id) nstrat,
               round(sum({NET})::numeric,2) net,
               count(*) FILTER (WHERE {NET} > 0) w
        FROM paper_positions p
        LEFT JOIN ai_strategies a ON a.strategy_id = p.strategy_id
        WHERE p.status='closed' AND p.closed_at >= '2026-09-15'
        GROUP BY 1 ORDER BY 2 DESC
    """)):
        print(f"  {str(x[0]):12s} 交易 {x[1]:3d} 笔 / {x[2]:2d} 策略  净={x[3]:>9}  胜率={x[4]/max(1,x[1])*100:5.1f}%")

    print("\n=== ai_strategies 中 learning_enabled=false 的全部行（17 条）===")
    for x in s.execute(text("""
        SELECT strategy_id, name, status, timeframe_tier, created_at
        FROM ai_strategies WHERE learning_enabled IS NOT TRUE ORDER BY status, strategy_id
    """)):
        print(f"  {x[0]:26s} {str(x[1])[:26]:26s} status={str(x[2])[:8]:8s} tier={str(x[3]):5s} {x[4]}")

    print("\n=== 这些'学习被关'的策略各自交易了多少（09-15 起，精确连接）===")
    for x in s.execute(text(f"""
        SELECT p.strategy_id, a.status, count(*) n, round(sum({NET})::numeric,2) net,
               count(*) FILTER (WHERE {NET} > 0) w
        FROM paper_positions p JOIN ai_strategies a ON a.strategy_id = p.strategy_id
        WHERE p.status='closed' AND p.closed_at >= '2026-09-15' AND a.learning_enabled IS NOT TRUE
        GROUP BY 1,2 ORDER BY n DESC
    """)):
        print(f"  {x[0]:26s} status={str(x[1])[:8]:8s} n={x[2]:3d} 净={x[3]:>9} 胜率={x[4]/max(1,x[2])*100:5.1f}%")
