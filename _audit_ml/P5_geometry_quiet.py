# -*- coding: utf-8 -*-
"""P5 入场几何 × 结果 + 安静子集净值检验（含手续费）。

问题：
1. 8/24 参数重标定后（271 笔）的 TP/SL 几何与结果。
2. 安静子集（vol_ratio<=1 且 adx_growth<=1）扣手续费后的净值。
3. 安静子集在「8/24 后」样本上是否仍成立（时间外）。
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
               unrealized_pnl, partial_realized_pnl, partial_fee_paid, close_reason,
               tp_price, sl_price, leverage, margin
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
    e, cp = float(r["entry_price"] or 0), float(r["close_price"] or 0)
    slp = float(r["sl_price"] or 0)
    tpp = float(r["tp_price"] or 0)
    sl_pct = abs(slp - e) / e * 100 if slp and e > 0 else None
    tp_pct = abs(tpp - e) / e * 100 if tpp and e > 0 else None
    gross = float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
    fees = float(r["partial_fee_paid"] or 0)
    notional = abs(float(r["margin"] or 0)) * float(r["leverage"] or 1)
    hold_min = (r["closed_at"] - r["opened_at"]).total_seconds() / 60 if r["closed_at"] else None
    recs.append({
        "side": r["side"], "gross": gross, "fees": fees, "net": gross - fees,
        "reason": r["close_reason"], "sl_pct": sl_pct, "tp_pct": tp_pct,
        "rr": (tp_pct / sl_pct if sl_pct else None), "hold_min": hold_min,
        "opened_at": r["opened_at"], "notional": notional, **f,
    })

print(f"样本 {len(recs)} 笔（有K线特征）")
allg = np.sum([r["gross"] for r in recs]); allf = np.sum([r["fees"] for r in recs])
print(f"全部: gross={allg:+.2f} fees={allf:.2f} net={allg-allf:+.2f}")

# ── 1. 几何 × 结果（全部）──
print("\n== SL 距离桶 × 结果 ==")
for lo, hi, lab in ((0, 0.014, "<1.4%"), (0.014, 0.018, "1.4-1.8%"), (0.018, 0.03, "1.8-3%"), (0.03, 99, ">=3%")):
    sub = [r for r in recs if r["sl_pct"] and lo <= r["sl_pct"] < hi]
    if sub:
        print(f"  {lab:>8}: n={len(sub):>4} net均={np.mean([r['net'] for r in sub]):+.3f} "
              f"sl率={sum(1 for r in sub if r['reason']=='sl')/len(sub)*100:4.1f}% "
              f"tp率={sum(1 for r in sub if r['reason']=='tp')/len(sub)*100:4.1f}%")

print("\n== TP 距离桶 × 结果 ==")
for lo, hi, lab in ((0, 0.008, "<0.8%"), (0.008, 0.011, "0.8-1.1%"), (0.011, 0.015, "1.1-1.5%"), (0.015, 99, ">=1.5%")):
    sub = [r for r in recs if r["tp_pct"] and lo <= r["tp_pct"] < hi]
    if sub:
        print(f"  {lab:>8}: n={len(sub):>4} net均={np.mean([r['net'] for r in sub]):+.3f} "
              f"tp率={sum(1 for r in sub if r['reason']=='tp')/len(sub)*100:4.1f}% "
              f"超时率={sum(1 for r in sub if r['reason'] in ('max_hold_timeout','hold_timeout_review'))/len(sub)*100:4.1f}%")

print("\n== 持仓时长桶 × 结果 ==")
for lo, hi, lab in ((0, 30, "<30min"), (30, 90, "30-90min"), (90, 180, "90-180min"), (180, 9999, ">=180min")):
    sub = [r for r in recs if r["hold_min"] is not None and lo <= r["hold_min"] < hi]
    if sub:
        print(f"  {lab:>9}: n={len(sub):>4} net均={np.mean([r['net'] for r in sub]):+.3f} "
              f"净合计={sum(r['net'] for r in sub):+.1f}")

# ── 2. 安静子集（扣费后）──
def quiet(r):
    return r["vol_ratio"] <= 1.0 and r["adx_growth"] <= 1.0

q = [r for r in recs if quiet(r)]
print(f"\n== 安静子集（扣费净值）== n={len(q)} gross={sum(r['gross'] for r in q):+.2f} "
      f"fees={sum(r['fees'] for r in q):.2f} net={sum(r['net'] for r in q):+.2f} "
      f"net均={np.mean([r['net'] for r in q]):+.3f}")
for side in ("long", "short"):
    qs = [r for r in q if r["side"] == side]
    if qs:
        print(f"  {side}: n={len(qs)} net={sum(r['net'] for r in qs):+.2f} "
              f"net均={np.mean([r['net'] for r in qs]):+.3f} sl率={sum(1 for r in qs if r['reason']=='sl')/len(qs)*100:.1f}%")
nq = [r for r in recs if not quiet(r)]
print(f"  非安静: n={len(nq)} net={sum(r['net'] for r in nq):+.2f} net均={np.mean([r['net'] for r in nq]):+.3f}")

# ── 3. 安静子集的时间外：8/24 前后 ──
recent_q = [r for r in q if r["opened_at"] >= pd.Timestamp("2026-08-24")]
old_q = [r for r in q if r["opened_at"] < pd.Timestamp("2026-08-24")]
for lab, sub in (("8/24 前", old_q), ("8/24 后（重标定参数）", recent_q)):
    if sub:
        print(f"  {lab}: n={len(sub)} net={sum(r['net'] for r in sub):+.2f} net均={np.mean([r['net'] for r in sub]):+.3f} "
              f"sl率={sum(1 for r in sub if r['reason']=='sl')/len(sub)*100:.1f}%")
for side in ("long", "short"):
    sub = [r for r in recent_q if r["side"] == side]
    if sub:
        print(f"  8/24后 {side}: n={len(sub)} net={sum(r['net'] for r in sub):+.2f} net均={np.mean([r['net'] for r in sub]):+.3f}")

# ── 4. 安静子集 × 几何（8/24 后）──
print("\n== 8/24 后安静子集 × SL 距离 ==")
for lo, hi, lab in ((0, 0.014, "<1.4%"), (0.014, 0.018, "1.4-1.8%"), (0.018, 99, ">=1.8%")):
    sub = [r for r in recent_q if r["sl_pct"] and lo <= r["sl_pct"] < hi]
    if sub:
        print(f"  {lab:>8}: n={len(sub):>4} net均={np.mean([r['net'] for r in sub]):+.3f} net={sum(r['net'] for r in sub):+.2f}")

# ── 5. 安静子集名义/佣金占比 ──
qn = [r["notional"] for r in q if r["notional"]]
print(f"\n安静子集名义中位={np.median(qn):.0f}，净/名义={np.sum([r['net'] for r in q])/np.sum([r['notional'] for r in q])*100:+.3f}%")
