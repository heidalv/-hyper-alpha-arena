# -*- coding: utf-8 -*-
"""出场事件时间线（Y8）：决策时刻 vs 实际成交时刻的缺口。

假设：long 车道「回撤保护」在慢周期上评估，决策触发后到真正成交之间
价格继续下滑 → 浮盈回吐成大亏。
"""
import os

from sqlalchemy import create_engine, text

eng = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    cols = [r[0] for r in c.execute(text("""
        select column_name from information_schema.columns
        where table_name='position_exit_events' order by ordinal_position
    """)).fetchall()]
    print("position_exit_events 列:", cols)
    print("\n近 14 天事件（按时间倒序，前 40）:")
    rows = c.execute(text("""
        select * from position_exit_events
        where created_at >= now() - interval '14 days'
        order by created_at desc limit 40
    """)).fetchall()
    for r in rows:
        d = dict(r._mapping)
        print("  ", {k: (str(v)[:40] if v is not None else None) for k, v in d.items()
                     if k in ("id", "position_id", "symbol", "action", "reason", "created_at",
                              "roi_pct", "pnl_pct", "price", "source", "tier")})
    print(f"\n近 14 天事件总数:",
          c.execute(text("select count(*) from position_exit_events where created_at >= now() - interval '14 days'")).scalar())
