# -*- coding: utf-8 -*-
"""多头历史样本探查（多头学习前置）。
统计 paper_positions / strategy_trades / trade_facts 中 mid/long 多头的样本量、
时间跨度、出场原因分布，判断学习样本是否充足。
"""
from sqlalchemy import create_engine, text
from collections import defaultdict

eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("== paper_positions mid/long ==")
    for r in c.execute(text("""
        select timeframe_tier, side, count(*) n,
               min(opened_at)::date, max(closed_at)::date
        from paper_positions
        where timeframe_tier in ('mid','long') and status='closed'
        group by timeframe_tier, side order by 1,2
    """)).fetchall():
        print("  ", tuple(r))
    print("\n== paper_positions close_reason (long) ==")
    for r in c.execute(text("""
        select close_reason, count(*) n,
               round(sum(unrealized_pnl + partial_realized_pnl - coalesce(partial_fee_paid,0))::numeric,2) net
        from paper_positions
        where timeframe_tier in ('mid','long') and side='long' and status='closed'
        group by close_reason order by n desc limit 25
    """)).fetchall():
        print("  ", tuple(r))
    print("\n== strategy_trades mid/long (真实信号) ==")
    for r in c.execute(text("""
        select timeframe_tier, side, count(*) n, min(opened_at)::date, max(closed_at)::date
        from strategy_trades
        where timeframe_tier in ('mid','long')
          and (trade_nature is null or trade_nature not like 'e2e_%')
        group by timeframe_tier, side order by 1,2
    """)).fetchall():
        print("  ", tuple(r))
    print("\n== trade_facts mid/long ==")
    for r in c.execute(text("""
        select tier, side, count(*) n, min(entry_ts)::date, max(close_ts)::date
        from trade_facts
        where tier in ('mid','long')
        group by tier, side order by 1,2
    """)).fetchall():
        print("  ", tuple(r))
