# -*- coding: utf-8 -*-
import time
import psycopg
from backend.services.market_maker.attribution import _market_dsn

now = int(time.time() * 1000)
lo = now - 120_000
with psycopg.connect(_market_dsn(), autocommit=True) as conn:
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*), coalesce(sum(qty),0), coalesce(sum(price*qty),0)"
        " FROM asterdex_trades WHERE symbol='BTWUSDT' AND event_ts_ms>%s",
        (lo,),
    )
    n, qty, usd = cur.fetchone()
    print("120s trades", n, "qty", float(qty), "usd", round(float(usd), 1))
    cur.execute(
        "SELECT count(*), coalesce(sum(qty),0) FROM asterdex_trades"
        " WHERE symbol='BTWUSDT' AND event_ts_ms>%s AND is_buyer_maker",
        (lo,),
    )
    print("120s seller hits", cur.fetchone())
    cur.execute(
        "SELECT bid_px, bid_qty, ask_px, ask_qty FROM asterdex_book_ticker"
        " WHERE symbol='BTWUSDT' AND bid_px>0 ORDER BY event_ts_ms DESC LIMIT 1"
    )
    b, bq, a, aq = (float(x) for x in cur.fetchone())
    print("touch", b, "bid_qty", bq, "ahead_usd", round(b * bq, 1), "ask", a, "spr_bp", round((a - b) / ((a + b) / 2) * 1e4, 2))
    cur.execute(
        "SELECT count(*), coalesce(sum(qty),0) FROM asterdex_trades"
        " WHERE symbol='BTWUSDT' AND event_ts_ms>%s AND is_buyer_maker AND price<=%s",
        (lo, b * 1.0001),
    )
    print("sells at/below current bid", cur.fetchone())
