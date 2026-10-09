# -*- coding: utf-8 -*-
import json
import time
from pathlib import Path

import psycopg

from backend.services.market_maker.attribution import _market_dsn
from backend.services.market_maker.flow_rules import (
    AHEAD_EDGES_USD, SPREAD_EDGES_BP, situation_band, situation_decision,
)

ROOT = Path(__file__).resolve().parents[1]
doc = json.loads((ROOT / "data" / "flow_situation_last.json").read_text(encoding="utf-8"))
syms = ["BTC", "ETH", "PLAY", "LYN", "BTW", "QNT", "PUMP", "ONE", "SI", "NIGHT"]
now = time.time()
with psycopg.connect(_market_dsn(), autocommit=True) as conn:
    cur = conn.cursor()
    for s in syms:
        cur.execute(
            "SELECT bid_px,bid_qty,ask_px,ask_qty FROM asterdex_book_ticker"
            " WHERE symbol=%s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms DESC LIMIT 1",
            (s + "USDT",),
        )
        row = cur.fetchone()
        if not row:
            print(s, "no book")
            continue
        b, bq, a, aq = (float(x) for x in row)
        mid = (b + a) / 2.0
        spr = (a - b) / mid * 1e4
        ab, aa = bq * b, aq * a
        d = situation_decision(doc, s, now, ab, aa, spr)
        print(
            f"{s:<8} spr {spr:7.2f}bp i{situation_band(spr, SPREAD_EDGES_BP)}  "
            f"bid ${ab:8.0f} i{situation_band(ab, AHEAD_EDGES_USD)}  "
            f"ask ${aa:8.0f} i{situation_band(aa, AHEAD_EDGES_USD)}  "
            f"{d['reason']}"
        )
