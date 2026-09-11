# -*- coding: utf-8 -*-
"""P5b 真实手续费核算：安静子集（vol_ratio<=1 且 adx_growth<=1）的真实净期望。

两套费用口径：
1. 台账口径：paper_orders.fee 实记（旧费率模型，Aster 被低估 8×）；
2. 真实口径：往返 8bp×名义（Aster taker 4bp×2 / binance 同量级）重算。
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
    pos_rows = [dict(r._mapping) for r in c.execute(text("""
        select strategy_id, symbol, side, entry_price, opened_at, closed_at,
               unrealized_pnl, partial_realized_pnl, close_reason, leverage, margin, original_size
        from paper_positions where strategy_id like 'scalp_mr_%' and status='closed'
        order by opened_at
    """)).fetchall()]
    fee_rows = [dict(r._mapping) for r in c.execute(text("""
        select strategy_id, sum(fee) as fee_total, sum(pnl) as pnl_total, count(*) as n_orders
        from paper_orders where strategy_id like 'scalp_mr_%'
        group by strategy_id
    """)).fetchall()]

fees_by_sid = {r["strategy_id"]: (float(r["fee_total"] or 0), int(r["n_orders"] or 0)) for r in fee_rows}


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
for r in pos_rows:
    ts = int(r["opened_at"].timestamp())
    f = guard_feats(r["symbol"], ts)
    if f is None:
        continue
    gross = float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
    notional = abs(float(r["margin"] or 0)) * float(r["leverage"] or 1)
    sid = r["strategy_id"]
    fee_rec, n_orders = fees_by_sid.get(sid, (0.0, 0))
    recs.append({
        "sid": sid, "side": r["side"], "gross": gross,
        "fee_rec": fee_rec, "n_orders": n_orders,
        "fee_true": notional * 0.0008,  # 8bp 往返（taker 4bp×2）
        "reason": r["close_reason"], "opened_at": r["opened_at"], "notional": notional,
        **f,
    })

n = len(recs)
print(f"样本 {n} 笔")
tot = {
    "gross": sum(r["gross"] for r in recs),
    "fee_rec": sum(r["fee_rec"] for r in recs),
    "fee_true": sum(r["fee_true"] for r in recs),
}
print(f"全部: gross={tot['gross']:+.2f} 台账费={tot['fee_rec']:.2f} 真实费估={tot['fee_true']:.2f} "
      f"真实净={tot['gross']-tot['fee_true']:+.2f}")

def quiet(r):
    return r["vol_ratio"] <= 1.0 and r["adx_growth"] <= 1.0

q = [r for r in recs if quiet(r)]
nq = [r for r in recs if not quiet(r)]

def stats(grp, name):
    g = sum(r["gross"] for r in grp)
    fr = sum(r["fee_rec"] for r in grp)
    ft = sum(r["fee_true"] for r in grp)
    print(f"{name}: n={len(grp)} gross={g:+.2f} 台账费={fr:.2f} 真实费估={ft:.2f} "
          f"真实净={g-ft:+.2f} 单笔真实净={(g-ft)/len(grp):+.3f}")

print("\n== 安静子集 vs 其余（真实费率口径）==")
stats(q, "安静(vol↓ & adx↓)")
stats(nq, "非安静")
for side in ("long", "short"):
    stats([r for r in q if r["side"] == side], f"  安静-{side}")
    stats([r for r in nq if r["side"] == side], f"  非安静-{side}")

recent_q = [r for r in q if r["opened_at"] >= pd.Timestamp("2026-08-24")]
old_q = [r for r in q if r["opened_at"] < pd.Timestamp("2026-08-24")]
print("\n== 安静子集时间外（真实费率口径）==")
stats(old_q, "8/24 前")
stats(recent_q, "8/24 后（重标定参数）")
stats([r for r in recent_q if r["side"] == "long"], "  8/24后 安静-long")
stats([r for r in recent_q if r["side"] == "short"], "  8/24后 安静-short")

# 台账费与真实费的口径对比（旧费率模型低估程度）
print(f"\n台账费/真实费 = {tot['fee_rec']/tot['fee_true']*100:.0f}%（旧模型低估 {tot['fee_true']/max(tot['fee_rec'],1e-9):.1f}×）")

# 名义汇总
qn = sum(r["notional"] for r in q)
print(f"安静子集名义合计={qn:.0f}，真实净/名义={sum(r['gross']-r['fee_true'] for r in q)/qn*100:+.3f}%")
