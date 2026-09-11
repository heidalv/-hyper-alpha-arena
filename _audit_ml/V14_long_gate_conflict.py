# -*- coding: utf-8 -*-
"""V14 长边两闸互斥性验证：mid 层在「日线 chop + agent ranging」下是否无单可开。

- 位置闸（midlong_location_gate）：regime ∈ (ranging,unknown) 且 pos24 ≥ 60% → 拒多
- 多头闸（midlong_circuit_gate, MIDLONG_LONG_MODE=learned, tiers=mid）：
    daily=chop 时要求 pos24 ≥ 60% 且 chg24 ≥ +2%
若两者同时生效 ⇒ pos≥60 被位置闸拒、pos<60 被多头闸拒 ⇒ 结构性死锁。
"""
import sys

import numpy as np
from sqlalchemy import create_engine, text

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "ADA", "AVAX", "UNI", "TON"]

kl = {}
d1 = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='300000'"))
    for s in SYMS:
        rows = c.execute(text("""select timestamp, open_price, high_price, low_price, close_price
                                 from crypto_klines where period='1h' and exchange='asterdex'
                                 and symbol=:s order by timestamp"""), {"s": s}).fetchall()
        if len(rows) > 100:
            kl[s] = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 100:
            d1[s] = [(int(r[0]), float(r[1])) for r in rows]

from backend.services.full_auto.midlong_circuit_gate import _daily_regime, _long_learned_ok  # noqa: E402
from backend.services.full_auto.midlong_location_gate import location_gate_check  # noqa: E402


def pos_chg(sym):
    s = kl.get(sym)
    if not s:
        return None, None, None, None
    win = s[-24:]
    hi = max(r[2] for r in win)
    lo = min(r[3] for r in win)
    px = s[-1][4]
    pos = (px - lo) / (hi - lo) * 100 if hi > lo else 50.0
    chg24 = (s[-1][4] / s[-25][4] - 1) * 100 if len(s) >= 25 and s[-25][4] > 0 else 0.0
    return px, hi, lo, (pos, chg24)


print(f"{'sym':>6} {'daily':>6} {'pos24':>7} {'chg24':>7} | {'多头闸(chop档)':<28} | {'位置闸(agent=ranging)':<40} | 结论")
deadlock = 0
for sym in SYMS:
    px, hi, lo, pc = pos_chg(sym)
    if px is None:
        continue
    pos, chg24 = pc
    dreg = _daily_regime(sym) or "?"
    lr_ok, lr_why = _long_learned_ok(sym, dreg)      # 真实 klines 口径
    ms = {sym: {"symbol": sym, "price": px, "range_24h_high": hi, "range_24h_low": lo,
                "price_change_24h_pct": chg24}}
    lg_ok, lg_why, _ = location_gate_check(
        sym, "buy", tier="mid", regime="ranging", market_summary=ms,
    )
    verdict = ""
    if dreg == "chop":
        if lr_ok and not lg_ok:
            verdict = "**死锁：多头闸放行/位置闸拒**"
            deadlock += 1
        elif (not lr_ok) and lg_ok:
            verdict = "**死锁：多头闸拒/位置闸放行**"
            deadlock += 1
        elif lr_ok and lg_ok:
            verdict = "两闸皆放行（可开）"
        else:
            verdict = "两闸皆拒"
    else:
        verdict = f"非 chop（{dreg}），多头闸不适用"
    print(f"{sym:>6} {dreg:>6} {pos:>7.1f} {chg24:>+7.2f} | {str(lr_ok):>5} {lr_why[:22]:<22} | "
          f"{str(lg_ok):>5} {lg_why[:34]:<34} | {verdict}")

print(f"\nchop 且两闸互斥的币数: {deadlock}")
