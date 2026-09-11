# -*- coding: utf-8 -*-
"""震荡行情策略强化证据：chop regime × 24h 区间位置 × 方向 的入场后 24h 收益。
复刻 _audit_ml/P1_chop.py 的 regime 口径 + 21_timing.py 的位置口径。
"""
from sqlalchemy import create_engine, text
from collections import defaultdict
import numpy as np

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

daily = {}
hourly = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1d' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 100:
            daily[s] = (np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1h' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, high_price, low_price, close_price from crypto_klines
                                 where period='1h' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 300:
            hourly[s] = (
                np.array([int(r[0]) for r in rows]),
                np.array([float(r[1]) for r in rows]),
                np.array([float(r[2]) for r in rows]),
                np.array([float(r[3]) for r in rows]),
            )


def reg(sym, ts):
    d = daily.get(sym)
    if d is None:
        return None
    t, c = d
    i = np.searchsorted(t, ts, side="right") - 1
    if i < 60 or i >= len(c):
        return None
    ema = np.mean(c[max(0, i - 200):i + 1])
    m60 = (c[i] / c[i - 60] - 1) if c[i - 60] > 0 else 0
    if c[i] > ema and m60 > 0.05:
        return "up"
    if c[i] < ema and m60 < -0.05:
        return "down"
    return "chop"


def pos24(sym, ts, px):
    h = hourly.get(sym)
    if h is None:
        return None
    t, hi, lo, cl = h
    i = np.searchsorted(t, ts, side="right") - 1
    if i < 24:
        return None
    w_hi = hi[i - 23:i + 1]
    w_lo = lo[i - 23:i + 1]
    span = w_hi.max() - w_lo.min()
    if span <= 0:
        return None
    return (px - w_lo.min()) / span


with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, original_size, size, partial_realized_pnl,
               opened_at, timeframe_tier, close_reason
        from paper_positions where timeframe_tier in ('mid','long') and status='closed'
        order by opened_at
    """)).fetchall()]

recs = []
for r in rows:
    try:
        e = float(r["entry_price"] or 0)
        cp = float(r["close_price"] or 0)
        sz = float(r["original_size"] or r["size"] or 0)
        if e <= 0 or cp <= 0 or sz <= 0:
            continue
    except (TypeError, ValueError):
        continue
    ts = int(r["opened_at"].timestamp())
    g = reg(r["symbol"], ts)
    if g is None:
        continue
    sgn = 1 if r["side"] == "long" else -1
    p = (cp - e) * sgn * sz + float(r["partial_realized_pnl"] or 0)
    pos = pos24(r["symbol"], ts, e)
    recs.append({"side": r["side"], "pnl": p, "reg": g, "pos": pos, "tier": r["timeframe_tier"]})

print(f"总样本 {len(recs)} 笔")
for g in ("up", "chop", "down"):
    sub = [r for r in recs if r["reg"] == g]
    print(f"\n=== regime={g} n={len(sub)} 合计 {sum(r['pnl'] for r in sub):+.2f} ===")
    for side in ("long", "short"):
        ss = [r for r in sub if r["side"] == side]
        if not ss:
            continue
        print(f"  {side} n={len(ss)} 合计 {sum(r['pnl'] for r in ss):+.2f}")
        for lo, hi in ((0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
            bb = [r for r in ss if r["pos"] is not None and lo <= r["pos"] < hi]
            npos = [r for r in ss if r["pos"] is not None]
            if bb:
                print(f"    分位[{lo:.1f},{hi:.1f}) n={len(bb)} 合计 {sum(r['pnl'] for r in bb):+.2f} "
                      f"均值 {np.mean([r['pnl'] for r in bb]):+.3f}")
        n_none = sum(1 for r in ss if r["pos"] is None)
        if n_none:
            print(f"    无位置数据 n={n_none}")

# 关键切片：chop 空仓 vs chop 高位拦截 的假设对比
ch = [r for r in recs if r["reg"] == "chop"]
print("\n=== chop 切片假设对比 ===")
ch_kept = [r for r in ch if not (r["side"] == "long" and r["pos"] is not None and r["pos"] >= 0.6)]
print(f"chop 全部: n={len(ch)} 合计 {sum(r['pnl'] for r in ch):+.2f}")
print(f"chop 剔除 long 且分位≥60%: n={len(ch_kept)} 合计 {sum(r['pnl'] for r in ch_kept):+.2f}")
ch_kept2 = [r for r in ch if not (r["side"] == "long")]
print(f"chop 只空头: n={len(ch_kept2)} 合计 {sum(r['pnl'] for r in ch_kept2):+.2f}")
ch_kept3 = [r for r in ch if not ((r["side"] == "long" and (r["pos"] is None or r["pos"] >= 0.6)))]
print(f"chop 剔除 long 分位≥60% 或未知: n={len(ch_kept3)} 合计 {sum(r['pnl'] for r in ch_kept3):+.2f}")
