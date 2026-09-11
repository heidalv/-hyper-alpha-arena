# -*- coding: utf-8 -*-
"""Z17：入场来源（模板）分布速查——为下一轮「入场侧」做准备。"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("=== 近 14 天 strategy_trades 的 template_id 分布 ===")
    for r in c.execute(text("""
        select coalesce(decision_context->>'template_id', '(none)') t, count(*) n
        from strategy_trades where opened_at >= now() - interval '14 days'
        group by 1 order by 2 desc limit 15
    """)).fetchall():
        print(f"  {str(r[0]):<48}{r[1]}")

    print("\n=== 近 30 天 mid/long 平仓笔数 ===")
    print("  ", c.execute(text("""
        select count(*) from paper_positions
        where timeframe_tier in ('mid','long') and status='closed'
          and closed_at >= now() - interval '30 days'
    """)).scalar())

    print("\n=== 近 75 天 mid/long 按 nature × 结局 ===")
    rows = c.execute(text("""
        select trade_nature, count(*) n,
               sum(coalesce(unrealized_pnl,0)+coalesce(partial_realized_pnl,0)
                   -coalesce(partial_fee_paid,0)) usd
        from paper_positions
        where timeframe_tier in ('mid','long') and status='closed'
          and closed_at >= now() - interval '75 days'
        group by 1 order by 3
    """)).fetchall()
    for r in rows:
        print(f"  {str(r[0] or '(null)'):<16}{r[1]:>5}{float(r[2] or 0):>+12.2f}")
