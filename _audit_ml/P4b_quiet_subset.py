# -*- coding: utf-8 -*-
"""P4b 守卫阈值网格 + 「安静区间」子集检验。

问题：把 vol_ratio 阈值从 2.5 降到 1.5/2.0 的拦截代价；以及
「低波动 + 低 ADX 增长」的安静子集是否有正期望（MR 唯一可能存活的口袋）。
"""
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

klines = {}
with MARKET.connect() as c:
    c.execute(text("set statement_timeout='600000'"))
    for s, in c.execute(text("select distinct symbol from crypto_klines where period='5m' and exchange='asterdex'")):
        rows = c.execute(text("""select timestamp, open_price, high_price, low_price, close_price
                                 from crypto_klines where period='5m' and exchange='asterdex'
                                 and symbol=:s order by timestamp"""), {"s": s}).fetchall()
        if len(rows) > 200:
            klines[s] = (
                np.array([int(r[0]) for r in rows]),
                np.array([float(r[1]) for r in rows]),
                np.array([float(r[2]) for r in rows]),
                np.array([float(r[3]) for r in rows]),
                np.array([float(r[4]) for r in rows]),
            )

with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, opened_at, closed_at,
               unrealized_pnl, partial_realized_pnl, close_reason, tp_price, sl_price
        from paper_positions where strategy_id like 'scalp_mr_%' and status='closed'
        order by opened_at
    """)).fetchall()]


def _adx(high, low, close, period=14):
    prev_high = np.roll(high, 1); prev_high[0] = high[0]
    prev_low = np.roll(low, 1); prev_low[0] = low[0]
    prev_close = np.roll(close, 1); prev_close[0] = close[0]
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    up = high - prev_high
    dn = prev_low - low
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = pd.Series(tr).ewm(alpha=1 / period, adjust=False, min_periods=period).mean().values
    pdi = 100 * pd.Series(plus_dm).ewm(alpha=1 / period, adjust=False, min_periods=period).mean().values / np.where(atr == 0, np.nan, atr)
    mdi = 100 * pd.Series(minus_dm).ewm(alpha=1 / period, adjust=False, min_periods=period).mean().values / np.where(atr == 0, np.nan, atr)
    denom = np.where((pdi + mdi) == 0, np.nan, pdi + mdi)
    dx = 100 * np.abs(pdi - mdi) / denom
    dx = np.nan_to_num(dx)
    return pd.Series(dx).ewm(alpha=1 / period, adjust=False, min_periods=period).mean().values


def guard_feats(sym, ts):
    d = klines.get(sym)
    if d is None:
        return None
    t, o, h, l, c = d
    i = int(np.searchsorted(t, ts, side="right") - 1)
    if i < 60 or i >= len(c) - 1:
        return None
    closes = c[:i + 1]
    recent = pd.Series(closes[-24:]).pct_change().dropna().std()
    prior = pd.Series(closes[-48:-24]).pct_change().dropna().std()
    vol_ratio = (recent / prior) if (np.isfinite(prior) and prior > 0) else (float("inf") if recent > 0 else 1.0)
    adx = _adx(h[:i + 1], l[:i + 1], c[:i + 1])
    cur_adx = float(adx[-1]) if len(adx) else np.nan
    prev_adx = float(adx[-25]) if len(adx) >= 25 else np.nan
    adx_growth = (cur_adx / prev_adx) if (np.isfinite(cur_adx) and np.isfinite(prev_adx) and prev_adx > 0) else 1.0
    return {"vol_ratio": vol_ratio, "adx": cur_adx, "adx_growth": adx_growth}


recs = []
for r in rows:
    ts = int(r["opened_at"].timestamp())
    f = guard_feats(r["symbol"], ts)
    if f is None:
        continue
    pnl = float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
    recs.append({"side": r["side"], "pnl": pnl, "reason": r["close_reason"], **f})

def hit(r, vth, ath, amin):
    return (r["vol_ratio"] > vth) or (r["adx_growth"] > ath and r["adx"] > amin)

print("== 阈值网格：拦截组均PnL / 放行组均PnL / 拦截率 / 拦掉的SL数 ==")
for vth in (1.5, 2.0, 2.5):
    for ath in (1.5, 1.8):
        for amin in (15.0, 25.0):
            b = [r for r in recs if hit(r, vth, ath, amin)]
            p = [r for r in recs if not hit(r, vth, ath, amin)]
            b_avg = np.mean([r["pnl"] for r in b])
            p_avg = np.mean([r["pnl"] for r in p])
            sl_b = sum(1 for r in b if r["reason"] == "sl")
            print(f"v={vth} a={ath} m={amin:>4}: 拦{len(b):>4}({len(b)/len(recs)*100:4.1f}%) "
                  f"均{b_avg:+.3f} | 放{len(p):>4} 均{p_avg:+.3f} | 拦SL={sl_b}")

print("\n== 安静子集（quiet）= vol_ratio<=1.0 且 adx_growth<=1.0 ==")
q = [r for r in recs if r["vol_ratio"] <= 1.0 and r["adx_growth"] <= 1.0]
print(f"n={len(q)} ({len(q)/len(recs)*100:.1f}%) 均PnL={np.mean([r['pnl'] for r in q]):+.4f} "
      f"胜率={sum(1 for r in q if r['pnl']>0.005)/len(q)*100:.1f}% "
      f"sl率={sum(1 for r in q if r['reason']=='sl')/len(q)*100:.1f}%")
for side in ("long", "short"):
    qs = [r for r in q if r["side"] == side]
    if qs:
        print(f"  {side}: n={len(qs)} 均PnL={np.mean([r['pnl'] for r in qs]):+.4f} "
              f"sl率={sum(1 for r in qs if r['reason']=='sl')/len(qs)*100:.1f}%")

# 安静子集的时间外：前/后半
mid = np.median([i for i, r in enumerate(recs) if r in q]) if False else None
qs = [r for r in q]
half = len(qs) // 2
for lab, sub in (("前半", qs[:half]), ("后半", qs[half:])):
    print(f"  {lab}: n={len(sub)} 均PnL={np.mean([r['pnl'] for r in sub]):+.4f} "
          f"sl率={sum(1 for r in sub if r['reason']=='sl')/len(sub)*100:.1f}%")

# 安静 + 双确认（pos极端）已在入场时天然满足；再叠加 SL 距离几何看看
print("\n== 安静子集 × 实际 SL 距离 ==")
for lo, hi, lab in ((0, 0.015, "<1.5%"), (0.015, 0.02, "1.5-2%"), (0.02, 9, ">=2%")):
    sub = []
    for r in q:
        # 需要 sl_price —— 从 rows 找对应行太重，这里跳过（见 P5）
        sub.append(r)
    # 无 SL 数据在此集合内，跳过细化
    break
print("（SL 距离细化在 P5 中做）")
