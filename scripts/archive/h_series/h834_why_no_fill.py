# -*- coding: utf-8 -*-
"""BTW 这张买单挂上之后，有没有卖单打到它，打到的量够不够排在前面的量。"""
import json
import time
from pathlib import Path

import psycopg

from backend.services.market_maker.attribution import _market_dsn

ROOT = Path(__file__).resolve().parents[1]
st = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
btw = (st.get("states") or {}).get("BTW") or {}
px = float(btw.get("quote_bid") or 0)
qts = float(btw.get("quote_ts") or 0)
print("quote", px, "age_s", round(time.time() - qts, 1) if qts else None)
if px <= 0 or qts <= 0:
    raise SystemExit(0)
lo = int(qts * 1000)
with psycopg.connect(_market_dsn(), autocommit=True) as conn:
    cur = conn.cursor()
    cur.execute(
        "SELECT bid_px, bid_qty, ask_px, ask_qty, event_ts_ms FROM asterdex_book_ticker"
        " WHERE symbol='BTWUSDT' AND bid_px>0 ORDER BY event_ts_ms DESC LIMIT 1"
    )
    book = cur.fetchone()
    print("book", book)
    cur.execute(
        "SELECT count(*), coalesce(sum(qty),0), min(price), max(price)"
        " FROM asterdex_trades WHERE symbol='BTWUSDT' AND event_ts_ms>%s",
        (lo,),
    )
    print("trades_since_quote", cur.fetchone())
    cur.execute(
        "SELECT count(*), coalesce(sum(qty),0) FROM asterdex_trades"
        " WHERE symbol='BTWUSDT' AND event_ts_ms>%s AND price<=%s AND is_buyer_maker",
        (lo, px * (1 + 1e-8)),
    )
    print("sells_at_or_through_our_bid", cur.fetchone())
    cur.execute(
        "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
        " WHERE symbol='BTWUSDT' AND event_ts_ms>%s ORDER BY event_ts_ms DESC LIMIT 8",
        (lo,),
    )
    for row in cur.fetchall():
        print(" ", row)
