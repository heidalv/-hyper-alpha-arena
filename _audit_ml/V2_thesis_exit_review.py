# -*- coding: utf-8 -*-
"""V2: thesis 出场逐笔复盘——若加 min_hold 闸，哪些会被拦下、哪些走紧急豁免。"""
import psycopg

CONN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"

with psycopg.connect(CONN, autocommit=True) as conn:
    conn.execute("set app.is_admin='on'")
    with conn.cursor() as cur:
        cur.execute("""
            select symbol, side, timeframe_tier, trade_nature,
                   round(margin::numeric,2) as margin,
                   round((unrealized_pnl+partial_realized_pnl)::numeric,2) as pnl,
                   round(((unrealized_pnl+partial_realized_pnl)/nullif(margin,0)*100)::numeric,2) as pnl_pct_margin,
                   round(extract(epoch from (closed_at-opened_at))/3600)::int as hold_h,
                   close_reason, opened_at, closed_at
            from paper_positions
            where account_id=14 and status='closed'
              and closed_at >= '2026-09-09 00:00:00'
              and (close_reason like 'thesis%')
            order by closed_at
        """)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

tier_min_hold = {"mid": 12, "long": 72}
tier_emerg = {"mid": 6.0, "long": 5.0}
print(f"{'sym':>8} {'side':>5} {'tier':>5} {'margin':>8} {'pnl':>8} {'pnl%保证金':>10} {'hold_h':>7} "
      f"{'min_hold':>8} {'紧急豁免':>8} {'加闸后':>10} reason")
for r in rows:
    d = dict(zip(cols, r))
    tier = (d["timeframe_tier"] or "").lower()
    mh = tier_min_hold.get(tier)
    em = tier_emerg.get(tier)
    pct = float(d["pnl_pct_margin"] or 0)
    hold = int(d["hold_h"] or 0)
    if mh is None:
        verdict = "不受闸(非mid/long)"
    elif hold >= mh:
        verdict = "原样平仓"
    elif em is not None and abs(pct) >= em:
        verdict = "紧急豁免→平仓"
    else:
        verdict = "**被拦下(持有)**"
    print(f"{d['symbol']:>8} {d['side']:>5} {tier:>5} {float(d['margin'] or 0):>8.2f} "
          f"{float(d['pnl'] or 0):>+8.2f} {pct:>+9.2f}% {hold:>7} {str(mh):>8} "
          f"{(str(em)+'%') if em else '-':>8} {verdict:>10} {str(d['close_reason'])[:28]}")
