# -*- coding: utf-8 -*-
"""多头准入门设计探针（第十六轮 W4）：up/chop/down × 位置 × 动量 交叉 + 时间切分。

前一轮（test_long_entry_conditions.py）发现：
  - up-regime 内 pos<60/RSI/chg 过滤有害（高位/高 chg 反而是动量延续，72h +2~3%）；
  - chop 的 pos<60 过滤组合 72h 转负（前段 +0.05% / 后段 -0.51%）；
  - down-regime 多头整体 flat。
本探针补齐决策所需格子：
  Q6: up 动量组合（pos≥60 / chg≥3 / 二者或）+ 时间切分；
  Q7: chop 全组合（无条件 / pos≥60 / pos≥60+chg≥2 / pos<60）+ 时间切分；
  Q8: down 无条件 + 时间切分（确认拦的正确性）。
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


def build_signals(h1, d1, sample_step=3, regime=("up",)):
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
            if reg not in regime:
                continue
            gains, losses = [], []
            for k in range(i - 14, i + 1):
                d = s[k][4] - s[k - 1][4]
                gains.append(max(d, 0.0))
                losses.append(max(-d, 0.0))
            ag = sum(gains) / 14.0
            al = sum(losses) / 14.0
            rsi = 100.0 - 100.0 / (1.0 + ag / al) if al > 0 else 100.0
            win = s[i - 23: i + 1]
            hi = max(x[2] for x in win)
            lo = min(x[3] for x in win)
            close = s[i][4]
            pos = (close - lo) / (hi - lo) * 100 if hi > lo else 50.0
            chg = (close / s[i - 24][4] - 1) * 100 if s[i - 24][4] > 0 else 0.0
            out.append((sym, i, ts, close, pos, rsi, chg))
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


def report(h1, sigs, name, fn, med_ts):
    rows = [(sym, i, ts) for sym, i, ts, px, pos, rsi, chg in sigs if fn(pos, rsi, chg)]
    if not rows:
        print(f"  {name:<32} 无样本")
        return
    r72 = [x for sym, i, _t in rows if (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
    rtr = [x for sym, i, _t in rows if (x := sim_trail(pick(h1, sym), i, 6, 3, 1.5, 168)) is not None]
    s72, strl = stats(r72), stats(rtr)
    ea = [x for sym, i, t in rows if t < med_ts and (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
    la = [x for sym, i, t in rows if t >= med_ts and (x := sim_fixed(pick(h1, sym), i, 72)) is not None]
    se, sl_ = stats(ea), stats(la)
    print(f"  {name:<32} n={s72['n']:>6} 72h={s72['mean']:>+7.3f}% 中位={s72['median']:>+7.3f}% "
          f"trail={strl['mean']:>+7.3f}%(胜{strl['win']:.2f}) | "
          f"前段={se['mean']:>+7.3f}%(n{se['n']:>5}) 后段={sl_['mean']:>+7.3f}%(n{sl_['n']:>5})")


def main() -> int:
    h1, d1 = load()

    print("=== Q6 up-regime 动量组合 ===")
    sigs = build_signals(h1, d1, regime=("up",))
    med = st.median(ts for _s, _i, ts, _p, _pos, _r, _c in sigs)
    print(f"up 信号 {len(sigs)}，中位时间 {datetime.fromtimestamp(med, tz=timezone.utc).date()}")
    report(h1, sigs, "U0 无条件", lambda p, r, c: True, med)
    report(h1, sigs, "U1 pos>=60", lambda p, r, c: p >= 60, med)
    report(h1, sigs, "U2 chg>=3", lambda p, r, c: c >= 3, med)
    report(h1, sigs, "U3 pos>=60 或 chg>=3", lambda p, r, c: p >= 60 or c >= 3, med)
    report(h1, sigs, "U4 pos<60 且 chg<3（非动量）", lambda p, r, c: p < 60 and c < 3, med)
    report(h1, sigs, "U5 接刀 chg<-5", lambda p, r, c: c < -5, med)
    report(h1, sigs, "U6 pos<60", lambda p, r, c: p < 60, med)

    print("\n=== Q7 chop-regime 组合 ===")
    sigs_c = build_signals(h1, d1, regime=("chop",))
    med_c = st.median(ts for _s, _i, ts, _p, _pos, _r, _c in sigs_c)
    print(f"chop 信号 {len(sigs_c)}，中位时间 {datetime.fromtimestamp(med_c, tz=timezone.utc).date()}")
    report(h1, sigs_c, "C0 无条件", lambda p, r, c: True, med_c)
    report(h1, sigs_c, "C1 pos>=60", lambda p, r, c: p >= 60, med_c)
    report(h1, sigs_c, "C2 pos>=60 且 chg>=2", lambda p, r, c: p >= 60 and c >= 2, med_c)
    report(h1, sigs_c, "C3 pos<60", lambda p, r, c: p < 60, med_c)
    report(h1, sigs_c, "C4 pos<60 且 chg>=-5", lambda p, r, c: p < 60 and c >= -5, med_c)
    report(h1, sigs_c, "C5 接刀 chg<-5", lambda p, r, c: c < -5, med_c)

    print("\n=== Q8 down-regime 组合（确认拦的正确性）===")
    sigs_d = build_signals(h1, d1, regime=("down",))
    med_d = st.median(ts for _s, _i, ts, _p, _pos, _r, _c in sigs_d)
    print(f"down 信号 {len(sigs_d)}，中位时间 {datetime.fromtimestamp(med_d, tz=timezone.utc).date()}")
    report(h1, sigs_d, "D0 无条件", lambda p, r, c: True, med_d)
    report(h1, sigs_d, "D1 pos<40（深跌接刀）", lambda p, r, c: p < 40, med_d)
    report(h1, sigs_d, "D2 chg<-5", lambda p, r, c: c < -5, med_d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
