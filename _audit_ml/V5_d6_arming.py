# -*- coding: utf-8 -*-
"""V5 profit_drawdown_full 的武装门槛核算：peak_usd vs 3%×position_value。"""
import psycopg

CONN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
with psycopg.connect(CONN, autocommit=True) as conn:
    conn.execute("set app.is_admin='on'")
    with conn.cursor() as cur:
        cur.execute("""
            select symbol, side, timeframe_tier, leverage,
                   entry_price, close_price, size, original_size, margin,
                   peak_unrealized_pnl, peak_pnl_pct, trough_unrealized_pnl,
                   (unrealized_pnl+partial_realized_pnl) as pnl, reduce_count, dca_count,
                   opened_at, closed_at,
                   round(extract(epoch from (closed_at-opened_at))/3600)::int as hold_h
            from paper_positions
            where account_id=14 and status='closed'
              and close_reason like 'profit_drawdown%' and closed_at >= '2026-08-20'
            order by closed_at
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

print(f"{'sym':>8} {'tier':>5} {'lev':>4} {'notional':>9} {'orig_notl':>10} {'peakUSD':>8} "
      f"{'3%门槛':>8} {'武装?':>6} {'1%名义':>8} {'实际亏损':>9} {'pnl':>8} {'red':>4} {'hold':>5}")
for r in rows:
    e = float(r["entry_price"] or 0)
    sz = float(r["size"] or 0)
    osz = float(r["original_size"] or sz or 0)
    notl = e * sz
    orig_notl = e * osz
    peak = float(r["peak_unrealized_pnl"] or 0)
    gate = notl * 0.03
    armed = peak >= gate
    flip = notl * 0.01
    pnl = float(r["pnl"] or 0)
    print(f"{r['symbol']:>8} {str(r['timeframe_tier']):>5} {float(r['leverage'] or 0):>4.0f} "
          f"{notl:>9.2f} {orig_notl:>10.2f} {peak:>8.2f} {gate:>8.2f} "
          f"{'YES' if armed else 'no':>6} {flip:>8.2f} {abs(pnl) if pnl<0 else 0:>9.2f} "
          f"{pnl:>+8.2f} {r['reduce_count']:>4} {r['hold_h']:>5}")
