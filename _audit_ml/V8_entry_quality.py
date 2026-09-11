# -*- coding: utf-8 -*-
"""V8 中长线入场质量核查：24h 区间分位 / regime / 门禁适用性 × 结果。

关键问题：9/9 位置闸（ranging/unknown 下 24h 分位≥60% 拒多）是否真的拦住了历史最差那一档？
新入场（9/9 之后）落在哪个分位？
"""
import numpy as np
import pandas as pd
import psycopg
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

kl1h = {}
kld = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1h' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, high_price, low_price, close_price from crypto_klines
                                 where period='1h' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 100:
            kl1h[s] = (np.array([int(r[0]) for r in rows]),
                       np.array([float(r[1]) for r in rows]),
                       np.array([float(r[2]) for r in rows]),
                       np.array([float(r[3]) for r in rows]))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1d' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 100:
            kld[s] = (np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))


def pos24(sym, ts, px):
    d = kl1h.get(sym)
    if d is None:
        return None, None, None
    t, h, l, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    if i < 24:
        return None, None, None
    hi = float(h[i - 23:i + 1].max())
    lo = float(l[i - 23:i + 1].min())
    chg24 = (px / float(c[i - 24]) - 1.0) if i >= 24 and c[i - 24] > 0 else None
    chg1 = (px / float(c[i - 1]) - 1.0) if i >= 1 and c[i - 1] > 0 else None
    if hi <= lo:
        return None, chg24, chg1
    return (px - lo) / (hi - lo), chg24, chg1


def daily_regime(sym, ts):
    d = kld.get(sym)
    if d is None:
        return None
    t, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    if i < 60 or i >= len(c):
        return None
    ema = float(np.mean(c[max(0, i - 200):i + 1]))
    m60 = (c[i] / c[i - 60] - 1) if c[i - 60] > 0 else 0
    if c[i] > ema and m60 > 0.05:
        return "up"
    if c[i] < ema and m60 < -0.05:
        return "down"
    return "chop"


def agent_regime(chg24, chg1):
    """复刻 RegimeAgent.classify_regime（15m 链路口径）。"""
    if chg24 is None:
        return "unknown"
    c24 = chg24 * 100.0
    c1 = (chg1 or 0.0) * 100.0
    if abs(c24) >= 12.0 or abs(c1) >= 5.0:
        return "extreme"
    if abs(c24) >= 4.0 and (c1 == 0 or c1 * c24 > 0):
        return "trend"
    return "ranging"


with ARENA.connect() as conn:
    conn.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in conn.execute(text("""
        select symbol, side, entry_price, close_price, opened_at, closed_at, status,
               timeframe_tier, trade_nature,
               (unrealized_pnl+partial_realized_pnl) as pnl,
               peak_pnl_pct, close_reason, original_size, size
        from paper_positions
        where account_id=14 and timeframe_tier in ('mid','long')
          and opened_at >= '2026-08-01'
        order by opened_at
    """)).fetchall()]

recs = []
for r in rows:
    e = float(r["entry_price"] or 0)
    ts = int(r["opened_at"].timestamp())
    p, chg24, chg1 = pos24(r["symbol"], ts, e)
    reg = agent_regime(chg24, chg1)
    dreg = daily_regime(r["symbol"], ts)
    pnl = float(r["pnl"] or 0)
    hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else None
    recs.append({**r, "pos24": p, "chg24": chg24, "chg1": chg1, "reg": reg, "dreg": dreg,
                 "pnl": pnl, "hold_h": hold_h})

print(f"样本 {len(recs)} 笔 mid/long 开仓（8/1 起）")

def show(name, sub):
    if not sub:
        return
    n = len(sub)
    win = sum(1 for r in sub if r["pnl"] > 0.005)
    loss = sum(1 for r in sub if r["pnl"] < -0.005)
    print(f"  {name:<34} n={n:>3} PnL合计={sum(r['pnl'] for r in sub):>+8.2f} "
          f"均={np.mean([r['pnl'] for r in sub]):>+6.2f} 胜/负={win}/{loss}")

print("\n== A) 按 24h 区间分位（位置闸口径）==")
for lo, hi, lab in ((0, 0.2, "0-20%"), (0.2, 0.4, "20-40%"), (0.4, 0.6, "40-60%"),
                    (0.6, 0.8, "60-80%"), (0.8, 1.01, "80-100%")):
    show(lab, [r for r in recs if r["pos24"] is not None and lo <= r["pos24"] < hi])
show("无位置数据", [r for r in recs if r["pos24"] is None])

print("\n== B) 按位置闸是否适用（regime ∈ ranging/unknown）==")
apply_ = [r for r in recs if r["reg"] in ("ranging", "unknown") and r["pos24"] is not None]
trend_ = [r for r in recs if r["reg"] == "trend" and r["pos24"] is not None]
ext_ = [r for r in recs if r["reg"] == "extreme"]
show("闸适用(ranging/unknown)", apply_)
show("  └ 其中分位≥60%（应被拒）", [r for r in apply_ if r["pos24"] >= 0.6])
show("  └ 其中分位<60%（放行）", [r for r in apply_ if r["pos24"] < 0.6])
show("闸不适用(trend)", trend_)
show("  └ trend 且分位≥60%", [r for r in trend_ if r["pos24"] >= 0.6])
show("extreme", ext_)

print("\n== C) 按日线 regime（circuit gate 口径）==")
for g in ("up", "chop", "down", None):
    show(str(g), [r for r in recs if r["dreg"] == g])

print("\n== D) 9/9 之后的新入场逐笔 ==")
for r in [x for x in recs if x["opened_at"].strftime("%Y-%m-%d") >= "2026-09-09"]:
    print(f"  {str(r['opened_at'])[:16]} {r['symbol']:>8} {r['side']:>5} tier={str(r['timeframe_tier']):>4} "
          f"pos24={('%.0f%%' % (r['pos24']*100)) if r['pos24'] is not None else 'n/a':>5} "
          f"chg24={('%+.1f%%' % (r['chg24']*100)) if r['chg24'] is not None else 'n/a':>7} "
          f"agent={r['reg']:>7} daily={str(r['dreg']):>5} "
          f"pnl={r['pnl']:>+7.2f} hold={('%dh' % r['hold_h']) if r['hold_h'] else 'open':>5} "
          f"{str(r['close_reason'] or '')[:26]}")

print("\n== E) 8/1–9/8 vs 9/9 后：分位分布对比 ==")
for lab, sub in (("8/1-9/8", [r for r in recs if r["opened_at"].strftime("%Y-%m-%d") < "2026-09-09"]),
                 ("9/9 后", [r for r in recs if r["opened_at"].strftime("%Y-%m-%d") >= "2026-09-09"])):
    ps = [r["pos24"] for r in sub if r["pos24"] is not None]
    if ps:
        print(f"  {lab}: n={len(sub)} 分位中位={np.median(ps)*100:.0f}% "
              f"≥60%占比={sum(1 for p in ps if p>=0.6)/len(ps)*100:.0f}%")
