# -*- coding: utf-8 -*-
"""多因子策略研究⑦：1h 趋势跟随族（唯一实证正 EV 层的多因子化）。

live 层里 trend_follow 是唯一正净利层（+8.19），本脚本检验：
  1h×12币×~5400根(225天)，EMA50>EMA200 趋势方向，多因子确认：
  F1 RSI 过滤（趋势方向 RSI≥55 / ≤45，骑趋势不追极端）
  F2 动量同向（mom12>0.3%）
  F3 放量（vol_ratio≥1.1）
  组合：基线(仅EMA) / +RSI / +RSI+MOM / +RSI+MOM+VOL
  退出：TP=2.0% / SL=1.0%（RR2），maxhold=24根(24h)，成本 10bps（真实 ~10-15bp）
输出：n/胜率/净/t值/前后50%/各币
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "ADA", "ARB", "XRP", "LINK", "AVAX", "SUI", "LTC"]
PERIOD = "1h"
EXCHANGE = "binance"
N_BARS = 6000
COST = 0.0010
MAXHOLD = 24       # 24×1h = 24h
COOLDOWN = 3       # 3h
TP_PCT = 0.020
SL_PCT = 0.010
EMA_FAST, EMA_SLOW = 50, 200
WIN = 48


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    up = np.maximum(delta, 0.0)
    dn = np.maximum(-delta, 0.0)
    ru = pd.Series(up).rolling(n).mean().to_numpy()
    rd = pd.Series(dn).rolling(n).mean().to_numpy()
    out = 100.0 - 100.0 / (1.0 + ru / np.maximum(rd, 1e-12))
    return np.nan_to_num(out, nan=50.0)


def load(sym: str):
    from backend.database.connection import MarketSessionLocal  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    with MarketSessionLocal() as db:
        rows = db.execute(text("""
            SELECT timestamp, open_price, high_price, low_price, close_price, volume
            FROM crypto_klines
            WHERE exchange = :ex AND symbol = :sym AND period = :per
              AND high_price IS NOT NULL AND low_price IS NOT NULL
            ORDER BY timestamp DESC LIMIT :lim
        """), {"ex": EXCHANGE, "sym": sym.upper(), "per": PERIOD, "lim": N_BARS}).fetchall()
    if not rows or len(rows) < 3000:
        return None
    rows = list(reversed(rows))
    df = pd.DataFrame({
        "ts": [r[0] for r in rows],
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
        "vol": [float(r[5]) for r in rows],
    })
    gap_ok = np.ones(len(df), dtype=bool)
    gap_ok[1:] = np.diff(df["ts"].to_numpy()) <= 3600 * 1.5
    return df, gap_ok


def sim(df, lm, sm, max_hold=MAXHOLD, cooldown=COOLDOWN):
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    trades = []
    i = 0
    while i < n - 1:
        is_long = bool(lm[i]) and not bool(sm[i])
        is_short = bool(sm[i]) and not bool(lm[i])
        if not (is_long or is_short):
            i += 1
            continue
        entry = close[i]
        if is_long:
            sl_price = entry * (1 - SL_PCT)
            tp_price = entry * (1 + TP_PCT)
        else:
            sl_price = entry * (1 + SL_PCT)
            tp_price = entry * (1 - TP_PCT)
        exit_i = None
        exit_reason = None
        exit_price = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            h, l = high[j], low[j]
            if is_long:
                if l <= sl_price:
                    exit_i, exit_reason, exit_price = j, "SL", sl_price
                    break
                if h >= tp_price:
                    exit_i, exit_reason, exit_price = j, "TP", tp_price
                    break
            else:
                if h >= sl_price:
                    exit_i, exit_reason, exit_price = j, "SL", sl_price
                    break
                if l <= tp_price:
                    exit_i, exit_reason, exit_price = j, "TP", tp_price
                    break
        if exit_i is None:
            exit_i = min(i + max_hold, n - 1)
            exit_reason = "TIMEOUT"
            exit_price = close[exit_i]
        ret = (exit_price / entry - 1.0) if is_long else (entry / exit_price - 1.0)
        ret -= COST
        trades.append(dict(reason=exit_reason, ret=ret))
        i = exit_i + cooldown
    return trades


def agg(trades):
    if not trades:
        return None
    r = np.array([t["ret"] for t in trades])
    n = len(r)
    se = r.std(ddof=1) / np.sqrt(n) if n > 1 else 0.0
    tstat = float(r.mean() / se) if se > 0 else 0.0
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return dict(n=n, wr=float((r > 0).mean()), avg=float(r.mean()), t=tstat,
                tp=reasons.get("TP", 0) / n, sl=reasons.get("SL", 0) / n,
                to=reasons.get("TIMEOUT", 0) / n)


def run():
    variants = ["基线(EMA)", "+RSI", "+RSI+MOM", "+RSI+MOM+VOL"]
    per_variant = {v: [] for v in variants}
    per_symbol = {}
    all_trades = {v: [] for v in variants}

    for sym in SYMBOLS:
        ld = load(sym)
        if ld is None:
            continue
        df, gap_ok = ld
        close = df["close"].to_numpy()
        n = len(df)
        ema_f = pd.Series(close).ewm(span=EMA_FAST, adjust=False).mean().to_numpy()
        ema_s = pd.Series(close).ewm(span=EMA_SLOW, adjust=False).mean().to_numpy()
        rsi_v = rsi(close, 14)
        mom12 = np.concatenate([[np.nan] * 12, close[12:] / close[:-12] - 1.0])
        vol_ratio = df["vol"].to_numpy() / np.maximum(pd.Series(df["vol"].to_numpy()).rolling(20).mean().to_numpy(), 1e-12)
        valid = (~np.isnan(ema_s)) & gap_ok

        trend_l = valid & (ema_f > ema_s) & (close > ema_s)
        trend_s = valid & (ema_f < ema_s) & (close < ema_s)
        f_rsi_l = rsi_v >= 55
        f_rsi_s = rsi_v <= 45
        f_mom_l = mom12 > 0.003
        f_mom_s = mom12 < -0.003
        f_vol_l = vol_ratio >= 1.1
        f_vol_s = vol_ratio >= 1.1

        combos = {
            "基线(EMA)": (trend_l, trend_s),
            "+RSI": (trend_l & f_rsi_l, trend_s & f_rsi_s),
            "+RSI+MOM": (trend_l & f_rsi_l & f_mom_l, trend_s & f_rsi_s & f_mom_s),
            "+RSI+MOM+VOL": (trend_l & f_rsi_l & f_mom_l & f_vol_l, trend_s & f_rsi_s & f_mom_s & f_vol_s),
        }
        for name, (lm_, sm_) in combos.items():
            tr = sim(df, lm_, sm_)
            st = agg(tr)
            if st:
                st["sym"] = sym
                st["name"] = name
                per_variant[name].append(st)
                all_trades[name].extend(tr)
                per_symbol.setdefault(sym, {})[name] = st

    print(f"\n===== 1h 趋势跟随族（{EXCHANGE} {len(SYMBOLS)}币, cost={COST*1e4:.0f}bps, TP={TP_PCT*100:.0f}%/SL={SL_PCT*100:.0f}%, maxhold={MAXHOLD}h） =====")
    print(f"{'变体':<16}{'n':>6} {'胜率':>7} {'净%':>7} {'t':>6} {'TP%':>5} {'SL%':>5} {'TO%':>5} {'前50%':>7} {'后50%':>7}")
    for name in variants:
        subs = per_variant.get(name, [])
        if not subs:
            continue
        w = sum(s["n"] for s in subs)
        awr = sum(s["wr"] * s["n"] for s in subs) / w
        aavg = sum(s["avg"] * s["n"] for s in subs) / w
        tp = sum(s["tp"] * s["n"] for s in subs) / w
        sl = sum(s["sl"] * s["n"] for s in subs) / w
        to = sum(s["to"] * s["n"] for s in subs) / w
        tr = all_trades[name]
        half = len(tr) // 2
        h1 = agg(tr[:half])
        h2 = agg(tr[half:])
        # 合并 t 值：对合并收益数组
        r = np.array([t_["ret"] for t_ in tr])
        se = r.std(ddof=1) / np.sqrt(len(r))
        tstat = float(r.mean() / se) if se > 0 else 0.0
        print(f"  {name:<16}{w:>6} {awr*100:>6.2f}% {aavg*100:>+6.3f}% {tstat:>+5.2f} {tp*100:>4.1f}% {sl*100:>4.1f}% {to*100:>4.1f}% {h1['avg']*100:>+6.3f}% {h2['avg']*100:>+6.3f}%")

    print("\n  各币 x 变体 净(%):")
    for sym in SYMBOLS:
        ps = per_symbol.get(sym)
        if not ps:
            continue
        parts = []
        for name in variants:
            st = ps.get(name)
            if st:
                parts.append(f"{name}:{st['avg']*100:+.2f}")
        print(f"    {sym:<5} " + "  ".join(parts))


if __name__ == "__main__":
    run()
