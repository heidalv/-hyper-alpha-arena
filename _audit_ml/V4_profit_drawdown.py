# -*- coding: utf-8 -*-
"""V4 profit_drawdown_full 深挖：时间分布 / 峰值 / 是否在 9/7 修复后仍触发。"""
import psycopg

CONN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
with psycopg.connect(CONN, autocommit=True) as conn:
    conn.execute("set app.is_admin='on'")
    with conn.cursor() as cur:
        cur.execute("""
            select symbol, side, timeframe_tier, trade_nature,
                   round((unrealized_pnl+partial_realized_pnl)::numeric,2) as pnl,
                   round((peak_pnl_pct*100)::numeric,3) as peak_pct,
                   round((trough_pnl_pct*100)::numeric,3) as trough_pct,
                   round(margin::numeric,2) as margin,
                   round(extract(epoch from (closed_at-opened_at))/3600)::int as hold_h,
                   opened_at, closed_at, reduce_count, tp_level_reached
            from paper_positions
            where account_id=14 and status='closed'
              and close_reason like 'profit_drawdown%'
              and closed_at >= '2026-08-20'
            order by closed_at
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

print(f"profit_drawdown_full 共 {len(rows)} 笔（8/20 起）")
print(f"{'关闭时间':<17} {'sym':>8} {'side':>5} {'tier':>5} {'pnl':>8} {'peak%':>8} {'trough%':>8} "
      f"{'margin':>8} {'hold_h':>6} {'reduce':>6} {'tp_lvl':>6}")
after_0907 = 0
for r in rows:
    ts = str(r["closed_at"])[:16]
    if r["closed_at"].strftime("%Y-%m-%d") >= "2026-09-07":
        after_0907 += 1
    print(f"{ts:<17} {r['symbol']:>8} {r['side']:>5} {str(r['timeframe_tier']):>5} "
          f"{float(r['pnl'] or 0):>+8.2f} {float(r['peak_pct'] or 0):>+8.3f} "
          f"{float(r['trough_pct'] or 0):>+8.3f} {float(r['margin'] or 0):>8.2f} "
          f"{r['hold_h']:>6} {r['reduce_count']:>6} {r['tp_level_reached']:>6}")
print(f"\n其中 9/7（跳过 D6 修复）之后: {after_0907} 笔")
