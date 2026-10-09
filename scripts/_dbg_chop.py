# -*- coding: utf-8 -*-
"""调试：同一批中线仓，用两套脚本的逻辑分别打 4h 震荡标签，逐笔对比 + 打印 mom24/dist50。"""
from __future__ import annotations

import datetime as dt
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    out = []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def load_positions(mode: str):
    with psycopg.connect(ARENA, autocommit=True) as ac:
        cur = ac.cursor()
        cur.execute("SET app.is_admin='on'")
        if mode == "opened":
            cur.execute(
                """select id, symbol, opened_at, unrealized_pnl, coalesce(partial_realized_pnl,0) pr
                   from paper_positions where account_id=14 and timeframe_tier='mid'
                     and opened_at > timestamp '2026-09-15 00:00:00' order by opened_at""")
        else:
            cur.execute(
                """select id, symbol, opened_at, unrealized_pnl, coalesce(partial_realized_pnl,0) pr
                   from paper_positions where account_id=14 and timeframe_tier='mid'
                     and status in ('closed','liquidated') and closed_at > timestamp '2026-09-15 00:00:00'
                   order by opened_at""")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def label(rows):
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cache = {}
        for t in rows:
            sym = t["symbol"]
            if sym not in cache:
                cur.execute(
                    """select timestamp, close_price from crypto_klines
                       where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                       order by timestamp""", (sym,))
                r = [(int(x[0]), float(x[1])) for x in cur.fetchall()]
                cache[sym] = (r, ema([c for _, c in r], 50)) if len(r) >= 60 else None
            c = cache.get(sym)
            if not c:
                t["kind"], t["mom24"], t["dist50"] = "nodata", None, None
                continue
            r, e50 = c
            ts = [x[0] for x in r]
            closes = [x[1] for x in r]
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            i = max((k for k, x in enumerate(ts) if x <= o), default=None)
            if i is None or i < 6:
                t["kind"], t["mom24"], t["dist50"] = "nodata", None, None
                continue
            mom24 = (closes[i] / closes[i - 6] - 1.0) * 100.0
            dist50 = (closes[i] / e50[i] - 1.0) * 100.0
            t["mom24"], t["dist50"] = mom24, dist50
            t["kind"] = "chop" if (abs(mom24) < 2.0 and abs(dist50) < 1.5) else "trend"
    return rows


for mode in ("opened", "closed"):
    rows = label(load_positions(mode))
    tot = {"chop": [0, 0.0], "trend": [0, 0.0], "nodata": [0, 0.0]}
    for t in rows:
        pnl = float(t["unrealized_pnl"] or 0) + float(t["pr"] or 0)
        tot[t["kind"]][0] += 1
        tot[t["kind"]][1] += pnl
    print("== 取样口径=%s：n=%d ==" % (mode, len(rows)))
    print("   chop  %d 笔 %+.2f | trend %d 笔 %+.2f | nodata %d"
          % (tot["chop"][0], tot["chop"][1], tot["trend"][0], tot["trend"][1], tot["nodata"][0]))
    print("   前 12 笔标签：")
    for t in rows[:12]:
        pnl = float(t["unrealized_pnl"] or 0) + float(t["pr"] or 0)
        print("     %-6s %s %-5s mom24=%-7s dist50=%-7s pnl=%+7.2f"
              % (t["symbol"], str(t["opened_at"])[5:16], t["kind"],
                 ("%+.2f" % t["mom24"]) if t["mom24"] is not None else "-",
                 ("%+.2f" % t["dist50"]) if t["dist50"] is not None else "-", pnl))
