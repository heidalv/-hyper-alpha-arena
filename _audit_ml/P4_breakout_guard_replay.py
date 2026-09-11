# -*- coding: utf-8 -*-
"""P4 突变守卫回放：在 1637 笔历史 MR 持仓入场时点回放 mr_regime_breakout_guard。

问题：
1. 守卫在亏损单（sl）入场时点的命中率 vs 盈利单命中率（判别力）？
2. 当前阈值(2.5/1.8/25)的拦截代价：误拦了多少盈利单？
3. 阈值扫描：更优的 vol_ratio / adx_ratio / adx_min 组合？
"""
import numpy as np
import pandas as pd
import psycopg
from sqlalchemy import create_engine, text

ARENA = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

# ── 载入 5m K 线（asterdex）──
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

# ── 载入 MR 持仓 ──
with ARENA.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows = [dict(r._mapping) for r in c.execute(text("""
        select symbol, side, entry_price, close_price, opened_at, closed_at,
               unrealized_pnl, partial_realized_pnl, close_reason, tp_price, sl_price
        from paper_positions where strategy_id like 'scalp_mr_%' and status='closed'
        order by opened_at
    """)).fetchall()]


def _rma(series, period):
    return series.astype(float).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


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
    if not np.isfinite(recent):
        recent = 0.0
    if not np.isfinite(prior) or prior <= 0:
        vol_ratio = float("inf") if recent > 0 else 1.0
    else:
        vol_ratio = recent / prior
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
    e, cp = float(r["entry_price"] or 0), float(r["close_price"] or 0)
    recs.append({
        "side": r["side"], "pnl": pnl, "reason": r["close_reason"],
        "vol_ratio": f["vol_ratio"], "adx": f["adx"], "adx_growth": f["adx_growth"],
    })

n = len(recs)
print(f"可回放入场特征：{n}/{len(rows)} 笔")

def hit(r, vth=2.5, ath=1.8, amin=25.0):
    return (r["vol_ratio"] > vth) or (r["adx_growth"] > ath and r["adx"] > amin)

# ── 1. 当前阈值判别力 ──
for grp_name, grp in [("sl 止损单", [r for r in recs if r["reason"] == "sl"]),
                      ("tp 盈利单", [r for r in recs if r["reason"] == "tp"]),
                      ("全部", recs)]:
    hits = sum(1 for r in grp if hit(r))
    print(f"{grp_name:>10}: n={len(grp):>4} 守卫命中={hits:>4} ({hits/len(grp)*100:4.1f}%) "
          f"合计PnL={sum(r['pnl'] for r in grp):+.2f}")

# ── 2. 阈值扫描 ──
print("\n== 阈值扫描（vol_ratio × adx_ratio × adx_min）：拦截掉的单的平均PnL / 放行的平均PnL ==")
best = None
for vth in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0):
    for ath in (1.3, 1.5, 1.8, 2.2, 2.6):
        for amin in (15.0, 20.0, 25.0, 30.0):
            blocked = [r for r in recs if hit(r, vth, ath, amin)]
            passed = [r for r in recs if not hit(r, vth, ath, amin)]
            if not blocked or not passed:
                continue
            b_avg = np.mean([r["pnl"] for r in blocked])
            p_avg = np.mean([r["pnl"] for r in passed])
            b_n = len(blocked)
            imp = b_avg - p_avg  # 拦截组越差（负越多）→ imp 越负越好
            if best is None or imp < best[0]:
                best = (imp, vth, ath, amin, b_avg, p_avg, b_n)
print(f"最优（拦截组平均PnL最低）：vol={best[1]} adx_ratio={best[2]} adx_min={best[3]} "
      f"拦截{best[6]}笔 拦截组均PnL={best[4]:+.3f} 放行组均PnL={best[5]:+.3f}")
print("\n当前阈值组合(2.5/1.8/25):")
cur = [(r["pnl"], r) for r in recs]
b = [r for r in recs if hit(r)]
p = [r for r in recs if not hit(r)]
print(f"  拦截 {len(b)} 笔 均PnL={np.mean([r['pnl'] for r in b]):+.3f} | "
      f"放行 {len(p)} 笔 均PnL={np.mean([r['pnl'] for r in p]):+.3f}")

# ── 3. vol_ratio 与 adx_growth 的分位与结果 ──
for feat in ("vol_ratio", "adx_growth"):
    vals = sorted(r[feat] for r in recs if np.isfinite(r[feat]))
    qs = np.quantile(vals, [0.5, 0.75, 0.9])
    print(f"\n{feat} 分位: p50={qs[0]:.2f} p75={qs[1]:.2f} p90={qs[2]:.2f}  "
          f"当前阈值 {'2.5' if feat=='vol_ratio' else '1.8'}")
    for lo, hi, lab in ((0, qs[0], "低"), (qs[0], qs[1], "中"), (qs[1], qs[2], "高"), (qs[2], 1e9, "极高")):
        sub = [r for r in recs if lo <= r[feat] < hi]
        if sub:
            print(f"  {lab:>3} n={len(sub):>4} 均PnL={np.mean([r['pnl'] for r in sub]):+.3f} "
                  f"sl率={sum(1 for r in sub if r['reason']=='sl')/len(sub)*100:.1f}%")
