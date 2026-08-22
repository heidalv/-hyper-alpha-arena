# -*- coding: utf-8 -*-
"""多因子策略研究⑧：方向不对称性解剖（15m 趋势回调MR + 5m MR 基线）。

研究⑤⑥发现 15m 趋势回调 MR 是唯一接近正边际的家族（+0.01~0.046%），但 t<1、
半段翻转。本脚本拆解其方向结构：
  - 多头子样本 vs 空头子样本（期间市场整体上涨 → 空头结构性逆风？）
  - 多头 × 趋势过滤强度（SMA vs EMA50/200 vs 无过滤）
  - RSI 阈值 40/35/30 扫描（多头）
输出：各子样本 n/胜率/净%/t值/TP/SL/TO。
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "ADA", "ARB", "XRP", "LINK", "AVAX", "SUI", "LTC"]
PERIOD = "15m"
EXCHANGE = "binance"
N_BARS = 20000
COST = 0.0009
WIN = 48
MAXHOLD = 24
COOLDOWN = 4

LOW_BAND, HIGH_BAND = 0.30, 0.70
RSI_OS, RSI_OB = 40.0, 60.0
AMP_LO, AMP_HI = 0.015, 0.050
TP_FRAC = 0.55
EDGE_BUF = 0.008
MIN_TP = 0.006
SL_FLOOR, SL_CAP = 0.006, 0.030
MIN_RR = 1.0


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
    gap_ok[1:] = np.diff(df["ts"].to_numpy()) <= 900 * 1.5
    return df, gap_ok


def mr_tp_sl(close, hi_win, lo_win, rp, tp_frac=TP_FRAC):
    n = len(close)
    tp_arr = np.full(n, np.nan)
    sl_arr = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(rp[i]):
            continue
        if rp[i] <= LOW_BAND:
            dist_far = max(hi_win[i] - close[i], 0.0)
            tpx = max(dist_far * tp_frac / close[i], MIN_TP)
            slx = max((close[i] - lo_win[i]) / close[i] + EDGE_BUF, SL_FLOOR)
        elif rp[i] >= HIGH_BAND:
            dist_far = max(close[i] - lo_win[i], 0.0)
            tpx = max(dist_far * tp_frac / close[i], MIN_TP)
            slx = max((hi_win[i] - close[i]) / close[i] + EDGE_BUF, SL_FLOOR)
        else:
            continue
        slx = min(slx, SL_CAP)
        cap_for_rr = tpx / MIN_RR
        if cap_for_rr >= SL_FLOOR:
            slx = min(slx, cap_for_rr)
        elif slx > 0 and (tpx / slx) < MIN_RR:
            tpx = min(0.04, max(tpx, slx * MIN_RR))
        tp_arr[i], sl_arr[i] = tpx, slx
    return tp_arr, sl_arr


def sim(df, lm, sm, tp_arr, sl_arr, max_hold=MAXHOLD, cooldown=COOLDOWN):
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
        if np.isnan(tp_arr[i]) or np.isnan(sl_arr[i]) or tp_arr[i] <= 0:
            i += 1
            continue
        entry = close[i]
        tp_pct, sl_pct = float(tp_arr[i]), float(sl_arr[i])
        if is_long:
            sl_price = entry * (1 - sl_pct)
            tp_price = entry * (1 + tp_pct)
        else:
            sl_price = entry * (1 + sl_pct)
            tp_price = entry * (1 - tp_pct)
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
        trades.append(dict(reason=exit_reason, ret=ret, side="long" if is_long else "short"))
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
    rows = []
    for sym in SYMBOLS:
        ld = load(sym)
        if ld is None:
            continue
        df, gap_ok = ld
        close = df["close"].to_numpy()
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        n = len(df)
        hi_win = pd.Series(high).rolling(WIN).max().to_numpy()
        lo_win = pd.Series(low).rolling(WIN).min().to_numpy()
        rng = np.where(hi_win - lo_win > 1e-12, hi_win - lo_win, np.nan)
        rp = np.where(np.isnan(rng), np.nan, (close - lo_win) / rng)
        rsi_v = rsi(close, 14)
        sma20 = pd.Series(close).rolling(20).mean().to_numpy()
        sma100 = pd.Series(close).rolling(100).mean().to_numpy()
        ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().to_numpy()
        ema200 = pd.Series(close).ewm(span=200, adjust=False).mean().to_numpy()
        amp = np.where(np.isnan(rng), np.nan, rng / close)
        amp_ok = (amp >= AMP_LO) & (amp <= AMP_HI)
        valid = (~np.isnan(rp)) & (~np.isnan(rsi_v)) & (~np.isnan(amp)) & gap_ok & amp_ok
        tp_arr, sl_arr = mr_tp_sl(close, hi_win, lo_win, rp)

        # 各变体（只给多头组合的 mask；空头另行统计）
        def make(rsi_lo, trend_mode):
            if trend_mode == "sma":
                tl = valid & ~np.isnan(sma100) & (close > sma100) & (sma20 > sma100)
            elif trend_mode == "ema":
                tl = valid & ~np.isnan(ema200) & (ema50 > ema200) & (close > ema200)
            else:
                tl = valid
            return tl & (rp <= LOW_BAND) & (rsi_v <= rsi_lo)

        variants = {
            "多_SMA_RSI40": (make(40, "sma"), None),
            "多_SMA_RSI35": (make(35, "sma"), None),
            "多_EMA_RSI40": (make(40, "ema"), None),
            "多_无过滤_RSI40": (make(40, "none"), None),
            "多_无过滤_RSI30": (make(30, "none"), None),
            "空_无过滤_RSI60": (None, valid & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB)),
            "空_SMA_RSI60": (None, valid & ~np.isnan(sma100) & (close < sma100) & (sma20 < sma100) & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB)),
            "空_EMA_RSI60": (None, valid & ~np.isnan(ema200) & (ema50 < ema200) & (close < ema200) & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB)),
        }
        for name, (lm_, sm_) in variants.items():
            tr = sim(df, lm_ if lm_ is not None else np.zeros(n, bool),
                     sm_ if sm_ is not None else np.zeros(n, bool), tp_arr, sl_arr)
            st = agg(tr)
            if st:
                st["sym"] = sym
                st["name"] = name
                rows.append(st)

    print(f"\n===== 方向不对称解剖（{PERIOD} {EXCHANGE} {len(SYMBOLS)}币, cost={COST*1e4:.0f}bps） =====")
    from collections import defaultdict
    groups = defaultdict(list)
    for r_ in rows:
        groups[r_["name"]].append(r_)
    print(f"{'变体':<18}{'n':>6}{'胜率':>7}{'净%':>8}{'t':>7}{'TP%':>6}{'SL%':>6}{'TO%':>6}")
    for name in ["多_SMA_RSI40", "多_SMA_RSI35", "多_EMA_RSI40", "多_无过滤_RSI40", "多_无过滤_RSI30",
                 "空_无过滤_RSI60", "空_SMA_RSI60", "空_EMA_RSI60"]:
        subs = groups.get(name, [])
        if not subs:
            continue
        w = sum(s["n"] for s in subs)
        awr = sum(s["wr"] * s["n"] for s in subs) / w
        aavg = sum(s["avg"] * s["n"] for s in subs) / w
        tp = sum(s["tp"] * s["n"] for s in subs) / w
        sl = sum(s["sl"] * s["n"] for s in subs) / w
        to = sum(s["to"] * s["n"] for s in subs) / w
        # 合并 t（用所有交易？此处用权重近似）
        print(f"  {name:<18}{w:>6}{awr*100:>6.2f}%{aavg*100:>+7.3f}%{'-':>7}{tp*100:>5.1f}%{sl*100:>5.1f}%{to*100:>5.1f}%")

    print("\n  各币 多头SMA_RSI40 vs 空头SMA_RSI60 净(%):")
    for sym in SYMBOLS:
        d = {}
        for r_ in rows:
            if r_["sym"] == sym:
                d[r_["name"]] = r_["avg"]
        lv = d.get("多_SMA_RSI40")
        sv = d.get("空_SMA_RSI60")
        if lv is not None:
            print(f"    {sym:<5} 多={lv*100:+.3f}%  空={sv*100 if sv is not None else float('nan'):+.3f}%")


if __name__ == "__main__":
    run()
