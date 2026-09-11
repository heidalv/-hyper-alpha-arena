# -*- coding: utf-8 -*-
"""P6/P7/P8 组合：chop 出场结构分解 + MR 校准曲线质量 + regime 标签诚实性。"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

# ── 日线 regime（与 P1/P2 同口径）──
daily = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='1d' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='1d' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 100:
            daily[s] = (np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))

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

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, original_size, size, partial_realized_pnl,
               opened_at, closed_at, timeframe_tier, trade_nature, close_reason
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
    g = reg(r["symbol"], int(r["opened_at"].timestamp()))
    if g is None:
        continue
    sgn = 1 if r["side"] == "long" else -1
    p = (cp - e) * sgn * sz + float(r["partial_realized_pnl"] or 0)
    hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else None
    recs.append({"side": r["side"], "pnl": p, "reg": g, "tier": r["timeframe_tier"],
                 "nature": r["trade_nature"], "reason": r["close_reason"], "hold_h": hold_h})

ch = [r for r in recs if r["reg"] == "chop"]
print(f"chop 样本 {len(ch)} 笔 合计 {sum(r['pnl'] for r in ch):+.2f}")

print("\n== P6a chop × trade_nature ==")
for nat in ("swing", "trend_follow", "position", None):
    sub = [r for r in ch if r["nature"] == nat]
    if sub:
        print(f"  {str(nat):>14}: n={len(sub):>3} 合计 {sum(r['pnl'] for r in sub):+8.2f} 均值 {np.mean([r['pnl'] for r in sub]):+.3f}")

print("\n== P6b chop × 方向 × close_reason ==")
from collections import defaultdict
agg = defaultdict(lambda: [0, 0.0])
for r in ch:
    k = (r["side"], r["reason"] or "?")
    agg[k][0] += 1
    agg[k][1] += r["pnl"]
for k in sorted(agg, key=lambda x: agg[x][1]):
    print(f"  {k[0]:>5} {k[1]:>22}: n={agg[k][0]:>3} 合计 {agg[k][1]:+8.2f}")

print("\n== P6c chop × 持仓时长 ==")
for lo, hi, lab in ((0, 6, "<6h"), (6, 24, "6-24h"), (24, 72, "24-72h"), (72, 9999, ">=72h")):
    sub = [r for r in ch if r["hold_h"] is not None and lo <= r["hold_h"] < hi]
    if sub:
        print(f"  {lab:>8}: n={len(sub):>3} 合计 {sum(r['pnl'] for r in sub):+8.2f} 均值 {np.mean([r['pnl'] for r in sub]):+.3f}")

# ── P7 MR 校准曲线质量 ──
print("\n== P7 scalp_composite_mr 校准样本：分数桶 × 胜率 ==")
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [r for r in c.execute(text("""
        select width_bucket(signal_value, 0, 100, 10) as b, count(*) as n,
               sum(case when trade_pnl > 0 then 1 else 0 end) as w
        from signal_trade_feedback where signal_type='scalp_composite_mr'
          and trade_pnl is not null group by 1 order by 1
    """)).fetchall()]
    for r in rows:
        print(f"  分数桶[{int(r[0])*10-10},{int(r[0])*10}): n={r[1]:>4} 胜率={r[2]/r[1]*100:5.1f}%")
    r2 = [r for r in c.execute(text("""
        select corr(signal_value, (trade_pnl>0)::int) from signal_trade_feedback
        where signal_type='scalp_composite_mr' and trade_pnl is not null
    """)).fetchall()]
    print(f"  corr(score, win) = {r2[0][0]:.4f}")

# ── P8 regime 标签诚实性：MR 入场时 classify_regime 看到的 1h/24h 涨跌 vs 入场后走向 ──
print("\n== P8 震荡标签诚实性（用 5m K 线重算 1h/24h 涨跌）==")
klines = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='5m' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, close_price from crypto_klines
                                 where period='5m' and exchange='asterdex' and symbol=:s order by timestamp"""),
                         {"s": s}).fetchall()
        if len(rows) > 400:
            klines[s] = (np.array([int(r[0]) for r in rows]), np.array([float(r[1]) for r in rows]))

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    mr_rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, opened_at, closed_at,
               unrealized_pnl, partial_realized_pnl, close_reason
        from paper_positions where strategy_id like 'scalp_mr_%' and status='closed'
        order by opened_at
    """)).fetchall()]

def ctx(sym, ts, entry):
    d = klines.get(sym)
    if d is None:
        return None
    t, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    if i < 300:
        return None
    chg1 = entry / c[i - 12] - 1 if c[i - 12] > 0 else 0.0
    chg24 = entry / c[i - 288] - 1 if c[i - 288] > 0 else 0.0
    return chg1, chg24

buckets = {"趋势市被误标震荡(入场|24h|>=4%)": [], "真震荡(入场|24h|<4%)": []}
for r in mr_rows:
    ts = int(r["opened_at"].timestamp())
    e = float(r["entry_price"] or 0)
    x = ctx(r["symbol"], ts, e)
    if x is None:
        continue
    chg1, chg24 = x
    pnl = float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
    if abs(chg24) >= 0.04:
        buckets["趋势市被误标震荡(入场|24h|>=4%)"].append((pnl, r))
    else:
        buckets["真震荡(入场|24h|<4%)"].append((pnl, r))

for name, items in buckets.items():
    if items:
        pnls = [p for p, _ in items]
        sls = sum(1 for _, r in items if r["close_reason"] == "sl")
        print(f"  {name}: n={len(items):>4} 合计PnL={sum(pnls):+8.2f} 均={np.mean(pnls):+.3f} "
              f"sl率={sls/len(items)*100:.1f}%")
