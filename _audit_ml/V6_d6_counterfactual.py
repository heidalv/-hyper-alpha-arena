# -*- coding: utf-8 -*-
"""V6 D6 基准修复反事实：旧口径 arming vs 新口径 arming + 出场后漂移。"""
import numpy as np
import psycopg
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

kl = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1h' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='1h' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 200:
            kl[s] = (np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, size, original_size,
               peak_unrealized_pnl, (unrealized_pnl+partial_realized_pnl) as pnl,
               closed_at
        from paper_positions
        where account_id=14 and status='closed'
          and close_reason like 'profit_drawdown%' and closed_at >= '2026-08-20'
        order by closed_at
    """)).fetchall()]


def fwd(sym, ts, cp, side, hours):
    d = kl.get(sym)
    if d is None or cp <= 0:
        return None
    t, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    j = i + hours
    if i < 0 or j >= len(c):
        return None
    raw = c[j] / cp - 1.0
    return raw if str(side).lower() in ("long", "buy") else -raw


print(f"{'sym':>8} {'pnl':>8} {'旧门槛':>7} {'旧武装':>6} {'新门槛':>7} {'新武装':>6} "
      f"{'变化':>6} {'出场后24h':>9} {'72h':>8}")
fixed_pnl = kept_pnl = 0.0
for r in rows:
    e = float(r["entry_price"] or 0)
    sz = float(r["size"] or 0)
    osz = float(r["original_size"] or sz or 0)
    peak = float(r["peak_unrealized_pnl"] or 0)
    old_gate = e * sz * 0.03
    new_gate = e * osz * 0.03
    old_armed = peak >= old_gate
    new_armed = peak >= new_gate
    pnl = float(r["pnl"] or 0)
    chg = "修复" if (old_armed and not new_armed) else ("保留" if new_armed else "-")
    if old_armed and not new_armed:
        fixed_pnl += pnl
    if new_armed:
        kept_pnl += pnl
    f24 = fwd(r["symbol"], int(r["closed_at"].timestamp()), float(r["close_price"] or 0), r["side"], 24)
    f72 = fwd(r["symbol"], int(r["closed_at"].timestamp()), float(r["close_price"] or 0), r["side"], 72)
    print(f"{r['symbol']:>8} {pnl:>+8.2f} {old_gate:>7.2f} {'Y' if old_armed else 'n':>6} "
          f"{new_gate:>7.2f} {'Y' if new_armed else 'n':>6} {chg:>6} "
          f"{(f'{f24*100:+.2f}%' if f24 is not None else 'n/a'):>9} "
          f"{(f'{f72*100:+.2f}%' if f72 is not None else 'n/a'):>8}")

print(f"\n不再武装（D6 不再平仓）的合计已实现 PnL: {fixed_pnl:+.2f}")
print(f"仍武装（真利润保护保留）的合计已实现 PnL: {kept_pnl:+.2f}")
