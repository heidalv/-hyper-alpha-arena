# -*- coding: utf-8 -*-
"""[2026-09-23] R4 变盘段止损单入场审计：12 连止损（进场即亏）是谁开的、入场时趋势面是什么。

对最近 25 笔 sl 平仓的 mid 仓：
  - 入场时 4h EMA21 位置（entry vs EMA21）与斜率（up/down/chop，同 regime_split 的标签B）
  - 模型方向（regime_direction，标签A）
  - 开仓订单的 strategy_id（若有）
  - 初始 SL 距离（sl_price vs entry）
"""
from __future__ import annotations

import io
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.database.connection import SessionLocal
from sqlalchemy import text
import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
ANALYTICS = "postgresql://laobao:alpha_pass@localhost:5432/alpha_analytics"
CST = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))


def ema_label(sym, o_ep):
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select timestamp, close_price from crypto_klines
               where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                 and timestamp between %s and %s order by timestamp""",
            (sym, o_ep - 20 * 86400, o_ep + 3600))
        bars = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
    upto = [c for (ts, c) in bars if ts <= o_ep]
    if len(upto) < 30:
        return None, None
    k = 2 / 22
    ema = upto[0]
    emas = []
    for c in upto:
        ema = ema + k * (c - ema)
        emas.append(ema)
    cnow = upto[-1]
    rising = emas[-1] > emas[-5]
    falling = emas[-1] < emas[-5]
    dist = (cnow - emas[-1]) / emas[-1] * 100
    if cnow > emas[-1] and rising:
        label = "up"
    elif cnow < emas[-1] and falling:
        label = "down"
    else:
        label = "chop"
    return label, dist


def model_dir(sym, o_ep):
    with psycopg.connect(ANALYTICS, autocommit=True) as ac:
        cur = ac.cursor()
        cur.execute("""select created_at, regime_direction from market_analysis_snapshots
                       where symbol=%s order by created_at""", (sym,))
        pick = None
        for ca, d in cur.fetchall():
            if ca is None:
                continue
            v = int(ca.replace(tzinfo=CST).timestamp())
            if v <= o_ep:
                pick = d
            else:
                break
        return pick


db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text(
        """SELECT p.id, p.symbol, p.leverage, p.entry_price, p.sl_price, p.close_price,
                  p.opened_at, p.closed_at, p.unrealized_pnl,
                  ROUND((p.unrealized_pnl/NULLIF(p.entry_price*p.size,0)*100*p.leverage)::numeric,1) pnl_pct,
                  p.close_reason
           FROM paper_positions p
           WHERE p.account_id=14 AND p.status IN ('closed','liquidated')
             AND COALESCE(p.timeframe_tier,'') ILIKE '%mid%'
             AND p.close_reason='sl'
           ORDER BY p.closed_at DESC LIMIT 25""")).mappings().all()
    print("n=%d 最近 sl 平仓 mid 仓（倒序）" % len(rows))
    print("%-6s %-7s %s %-4s %-6s %+8s %-7s %+6s %+6s %5s %s"
          % ("pos", "symbol", "lev", "side", "4h标签", "距EMA21%", "模型方向", "SL距%", "pnl%", "持仓h", "开仓时间"))
    stats = {"up": 0, "down": 0, "chop": 0, "nd": 0}
    above = 0
    for r in rows:
        o_ep = int(r["opened_at"].replace(tzinfo=CST).timestamp())
        lab, dist = ema_label(r["symbol"], o_ep)
        md = model_dir(r["symbol"], o_ep)
        sl_dist = (float(r["sl_price"]) / float(r["entry_price"]) - 1.0) * 100.0 if r["sl_price"] else None
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600.0
        stats[lab or "nd"] += 1
        if dist is not None and dist > 0:
            above += 1
        print("%-6s %-7s %sx   %s   %-4s    %+6.2f   %-7s  %+5.2f  %+5.1f  %4.1fh  %s" % (
            r["id"], r["symbol"], r["leverage"], "long",
            lab, dist if dist is not None else float("nan"), md or "-",
            sl_dist if sl_dist is not None else float("nan"),
            float(r["pnl_pct"] or 0), hold_h, str(r["opened_at"])[5:16]))
    print("4h标签分布: %s   入场价在 EMA21 上方(追高)的笔数: %d/%d" % (stats, above, len(rows)))
    # 开仓订单 strategy_id
    rows2 = db.execute(text(
        """SELECT p.id, o.strategy_id
           FROM paper_positions p
           JOIN LATERAL (
             SELECT strategy_id FROM paper_orders o
             WHERE o.account_id=p.account_id AND o.symbol=p.symbol
               AND o.created_at BETWEEN p.opened_at - interval '5 minutes'
                                    AND p.opened_at + interval '5 minutes'
               AND o.pnl IS NULL
             ORDER BY o.created_at LIMIT 1) o ON true
           WHERE p.id = ANY(:ids)"""),
        {"ids": tuple(int(r["id"]) for r in rows)}).mappings().all()
    print("开仓订单 strategy_id: ", {r["id"]: r["strategy_id"] for r in rows2})
finally:
    db.close()
