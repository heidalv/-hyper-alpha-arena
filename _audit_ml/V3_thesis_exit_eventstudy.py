# -*- coding: utf-8 -*-
"""V3 thesis 出场事件研究：出场后 24h/72h 方向口径收益（决定该不该拦）。

若出场后价格继续按持仓反方向走（fwd 为负）→ 出场正确（省了钱）；
若出场后价格回到持仓方向（fwd 为正）→ 出场砍错了（毁价值）。
"""
import numpy as np
import psycopg
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

# 1h K 线缓存
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
        select symbol, side, close_price, closed_at, close_reason, timeframe_tier,
               unrealized_pnl + partial_realized_pnl as pnl
        from paper_positions
        where account_id=14 and status='closed'
          and closed_at >= '2026-08-20' and timeframe_tier in ('mid','long')
        order by closed_at
    """)).fetchall()]


def fwd_ret(sym, ts, cp, side, hours):
    d = kl.get(sym)
    if d is None or cp <= 0:
        return None
    t, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    j = i + hours
    if i < 0 or i >= len(c) or j >= len(c):
        return None
    raw = c[j] / cp - 1.0
    sgn = 1.0 if str(side).lower() in ("long", "buy") else -1.0
    return sgn * raw  # 持仓方向口径：正=继续朝我有利方向


groups = {}
for r in rows:
    cr = str(r["close_reason"] or "")
    if cr.startswith("thesis_invalidation"):
        g = "thesis_invalidation"
    elif cr.startswith("thesis_should_close"):
        g = "thesis_should_close"
    elif cr.startswith("trend_broken"):
        g = "trend_broken"
    elif cr.startswith("sl") or "sl_pct" in cr:
        g = "sl(硬止损)"
    elif cr.startswith("profit_drawdown"):
        g = "profit_drawdown_full"
    else:
        g = "其他"
    f24 = fwd_ret(r["symbol"], int(r["closed_at"].timestamp()), float(r["close_price"] or 0), r["side"], 24)
    f72 = fwd_ret(r["symbol"], int(r["closed_at"].timestamp()), float(r["close_price"] or 0), r["side"], 72)
    groups.setdefault(g, []).append((float(r["pnl"] or 0), f24, f72, r))

print(f"{'出场原因':<22} {'n':>3} {'实现PnL':>9} {'出场后24h(方向口径)':>20} {'出场后72h':>18}  判定")
for g, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    f24s = [x[1] for x in items if x[1] is not None]
    f72s = [x[2] for x in items if x[2] is not None]
    pnl = sum(x[0] for x in items)
    m24 = float(np.mean(f24s)) * 100 if f24s else float("nan")
    m72 = float(np.mean(f72s)) * 100 if f72s else float("nan")
    win24 = sum(1 for v in f24s if v > 0) / len(f24s) * 100 if f24s else 0
    verdict = "出场正确(后续继续逆行)" if m24 < 0 else "出场过早(后续回归)"
    print(f"{g:<22} {len(items):>3} {pnl:>+9.2f} {m24:>+19.2f}% {m72:>+17.2f}%  {verdict} (24h胜率{win24:.0f}%)")

print("\n== thesis 出场逐笔 ==")
for g in ("thesis_invalidation", "thesis_should_close"):
    for pnl, f24, f72, r in groups.get(g, []):
        print(f"  {g:<20} {str(r['closed_at'])[:16]} {r['symbol']:>8} {r['side']:>5} "
              f"pnl={pnl:+8.2f} 之后24h={('%+.2f%%' % (f24*100)) if f24 is not None else 'n/a':>8} "
              f"72h={('%+.2f%%' % (f72*100)) if f72 is not None else 'n/a':>8}")
