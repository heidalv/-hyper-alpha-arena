# -*- coding: utf-8 -*-
"""learned 多头闸最终配置验证（W5）：U2(up+chg24≥3) 与 C2(chop+pos≥60+chg≥2)。

对两个胜者配置做：
  1. trail（SL6/3-1.5/168h）时间切分（前后段）；
  2. 非重叠采样（i%24==0，72h 口径）；
  3. per-symbol trail 净均值分布；
  4. 与「无条件」基线的对比。
"""
from __future__ import annotations

import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")

SYMS = ["BTC","ETH","SOL","BNB","XRP","DOGE","ADA","AVAX","LINK","DOT","LTC","TON",
        "TRX","ATOM","BCH","ETC","UNI","AAVE","ARB","OP","SUI","APT","NEAR","INJ",
        "SEI","CRV","ASTER","XPL","VIRTUAL","ZEC"]

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001
START_TS = int(datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp())


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load():
    h1 = defaultdict(list)
    d1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": SYMS}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1d' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": SYMS}).fetchall():
                d1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1, d1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 500:
            return v
    return None


def daily_regime_at(dseries, ts):
    if not dseries:
        return "unknown"
    i = next((k for k, row in enumerate(dseries) if row[0] >= ts), len(dseries) - 1)
    if i > 0:
        i -= 1
    if i < 60:
        return "unknown"
    closes = [row[4] for row in dseries[max(0, i - 200): i + 1]]
    ema = sum(closes) / len(closes)
    px = closes[-1]
    base = dseries[i - 60][4] if i >= 60 else closes[0]
    mom60 = (px / base - 1.0) if base > 0 else 0.0
    if px > ema and mom60 > 0.05:
        return "up"
    if px < ema and mom60 < -0.05:
        return "down"
    return "chop"


def build_signals(h1, d1, sample_step=3):
    out = []
    for sym in SYMS:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if not s or not ds:
            continue
        for i in range(100, len(s) - 1, sample_step):
            ts = s[i][0]
            if ts < START_TS:
                continue
            reg = daily_regime_at(ds, ts)
            win = s[i - 23: i + 1]
            hi = max(x[2] for x in win)
            lo = min(x[3] for x in win)
            close = s[i][4]
            pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50.0
            chg = (close / s[i - 24][4] - 1) * 100 if s[i - 24][4] > 0 else 0.0
            out.append((sym, i, ts, close, reg, pos, chg))
    return out


def sim_fixed(s, i, h):
    if i + h >= len(s):
        return None
    entry = s[i][4]
    return (s[i + h][4] - entry) / entry * 100 - cost_pct(h)


def sim_trail(s, i, sl_pct, trig, gap, max_h):
    entry = s[i][4]
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (h - entry) / entry * 100
        peak = max(peak, mfe)
        if l <= entry * (1 - sl_pct / 100):
            return -sl_pct - cost_pct(hold)
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold)
        if k == min(i + int(max_h), len(s) - 1):
            return (c - entry) / entry * 100 - cost_pct(hold)
    return None


def stats(rows):
    if not rows:
        return None
    nets = [r for r in rows]
    return {"n": len(nets), "mean": round(sum(nets) / len(nets), 3),
            "median": round(st.median(nets), 3),
            "win": round(sum(1 for x in nets if x > 0) / len(nets), 3)}


def deep(h1, sigs, name, fn):
    rows = [(s, i, t) for s, i, t, px, reg, pos, chg in sigs if fn(reg, pos, chg)]
    med = st.median(t for _s, _i, t in rows) if rows else 0
    ea = [(s, i) for s, i, t in rows if t < med]
    la = [(s, i) for s, i, t in rows if t >= med]
    print(f"\n=== {name}（n={len(rows)}，中位时间 {datetime.fromtimestamp(med, tz=timezone.utc).date()}）===")
    for label, v in [("前段", ea), ("后段", la)]:
        r72 = [x for s, i in v if (x := sim_fixed(pick(h1, s), i, 72)) is not None]
        rtr = [x for s, i in v if (x := sim_trail(pick(h1, s), i, 6, 3, 1.5, 168)) is not None]
        s72, strl = stats(r72), stats(rtr)
        print(f"  {label}: 72h={s72['mean']:>+7.3f}%(n{s72['n']:>5},中位{s72['median']:>+7.3f}) | "
              f"trail={strl['mean']:>+7.3f}%(n{strl['n']:>5},中位{strl['median']:>+7.3f},胜{strl['win']:.2f})")
    non = [(s, i) for s, i, t in rows if (i - 100) % 24 == 0]
    r72n = [x for s, i in non if (x := sim_fixed(pick(h1, s), i, 72)) is not None]
    rtrn = [x for s, i in non if (x := sim_trail(pick(h1, s), i, 6, 3, 1.5, 168)) is not None]
    s72n, strln = stats(r72n), stats(rtrn)
    print(f"  非重叠(i%24==0): 72h={s72n['mean']:>+7.3f}%(n{s72n['n']:>5}) | "
          f"trail={strln['mean']:>+7.3f}%(n{strln['n']:>5},胜{strln['win']:.2f})")
    bysym = defaultdict(list)
    for s, i, _t in rows:
        v = sim_trail(pick(h1, s), i, 6, 3, 1.5, 168)
        if v is not None:
            bysym[s].append(v)
    pos_n, neg_n, tot = 0, 0, 0
    negs = []
    for s in sorted(bysym, key=lambda k: -len(bysym[k])):
        v = bysym[s]
        m = sum(v) / len(v)
        tot += 1
        if m > 0:
            pos_n += 1
        else:
            neg_n += 1
            negs.append(f"{s}({m:+.2f}%,n{len(v)})")
    print(f"  逐币 trail: 正 {pos_n}/{tot}，负 {neg_n}/{tot}" + (f"：{', '.join(negs[:8])}" if negs else ""))


def main() -> int:
    h1, d1 = load()
    sigs = build_signals(h1, d1)
    print(f"总信号（all regimes, 2025-07 起）: {len(sigs)}")
    deep(h1, sigs, "U2 胜者：up + chg24≥3%", lambda reg, p, c: reg == "up" and c >= 3)
    deep(h1, sigs, "C2 胜者：chop + pos24≥60% + chg24≥2%", lambda reg, p, c: reg == "chop" and p >= 60 and c >= 2)
    deep(h1, sigs, "基线对照：up 无条件", lambda reg, p, c: reg == "up")
    deep(h1, sigs, "基线对照：chop 无条件", lambda reg, p, c: reg == "chop")
    deep(h1, sigs, "对照：down 无条件（应拦）", lambda reg, p, c: reg == "down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
