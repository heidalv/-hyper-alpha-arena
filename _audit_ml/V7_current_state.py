# -*- coding: utf-8 -*-
"""V7 当前实况：9/11 之后中长线是否恢复开仓（paper_skip 生效后）。"""
import psycopg

CONN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
with psycopg.connect(CONN, autocommit=True) as conn:
    conn.execute("set app.is_admin='on'")
    with conn.cursor() as cur:
        cur.execute("""
            select symbol, side, timeframe_tier, trade_nature, opened_at, status, strategy_id
            from paper_positions
            where account_id=14 and opened_at >= '2026-09-10 23:00:00'
            order by opened_at desc limit 20
        """)
        rows = cur.fetchall()
        print(f"9/10 23:00 后新开仓: {len(rows)} 笔")
        for r in rows:
            print("  ", str(r[4])[:16], r[0], r[1], "tier=", r[2], r[3], r[5])
        cur.execute("""
            select count(*), min(opened_at), max(opened_at)
            from paper_positions
            where account_id=14 and opened_at >= '2026-09-11 00:00:00'
        """)
        print("今日(9/11)开仓统计:", cur.fetchone())
        cur.execute("""
            select symbol, side, timeframe_tier, close_reason,
                   round((unrealized_pnl+partial_realized_pnl)::numeric,2) as pnl, closed_at
            from paper_positions
            where account_id=14 and status='closed' and closed_at >= '2026-09-11 00:00:00'
            order by closed_at desc limit 10
        """)
        print("\n今日平仓:")
        for r in cur.fetchall():
            print("  ", str(r[5])[:16], r[0], r[1], "tier=", r[2], "pnl=", r[3], str(r[4])[:28])
