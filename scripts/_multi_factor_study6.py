# -*- coding: utf-8 -*-
"""多因子策略研究⑥：15m 趋势回调MR 深度验证 + 边沿限价入场。

研究⑤发现唯一带正边际（9bp 成本下 +0.046%/笔）的家族：15m 趋势回调 MR。
本脚本验证其稳健性并比较入场方式：
  V0 复现：15m 48根窗口(12h)，趋势回调过滤，市价入场（基准，与⑤同口径）
  V1 边沿限价：信号后若 bar 内 low≤限价(swing_low+buf) 则以限价成交（maker 费率0.02%，
     滑点视为 0），最多等 6 根；未成交放弃 —— 真实「贴在边沿等回调」
  V2 更强趋势过滤：EMA50>EMA200 且 close>EMA200（替代 SMA 组合）
  V3 不同 RSI 阈值 35/50（更早介入）
  V4 振幅下界调整：MR 只做 [1.5%,6%]，上界放宽
输出：n / 净 / 胜率 / t值 / 前后50% 稳定性 / 各币明细 / 限价成交率
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
BASE_COST = 0.0009      # 市价往返（taker 9bp）
LIMIT_COST = 0.0004     # 限价往返（maker 2bp×2 + 无滑点）
WIN = 48
MAXHOLD = 24            # 24×15m = 6h
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


def sim(df, lm, sm, tp_arr, sl_arr, cost, max_hold=MAXHOLD, cooldown=COOLDOWN,
        limit_fill=None, limit_wait=6, lo_win_arr=None, hi_win_arr=None):
    """limit_fill=None → 市价入场；否则 ('edge',) 用限价（long: limit=swing_low*(1+buf*0.5)）."""
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    trades = []
    filled = 0
    signals = 0
    i = 0
    while i < n - 1:
        is_long = bool(lm[i]) and not bool(sm[i])
        is_short = bool(sm[i]) and not bool(lm[i])
        if not (is_long or is_short):
            i += 1
            continue
        signals += 1
        if np.isnan(tp_arr[i]) or np.isnan(sl_arr[i]) or tp_arr[i] <= 0:
            i += 1
            continue
        # 边沿限价：只需要当前窗口的低/高沿
        if limit_fill:
            # 在信号 bar 的窗口沿挂单（复用同一窗口近似；真实实现看 next bars）
            entry = None
            esc = None
            if is_long:
                lim_price = lo_win_arr[i] * (1 + EDGE_BUF)  # 低沿+半个缓冲，允许深回调
            else:
                lim_price = hi_win_arr[i] * (1 - EDGE_BUF)
            for j in range(i + 1, min(i + 1 + limit_wait, n)):
                if is_long and low[j] <= lim_price:
                    entry, esc = lim_price, j
                    break
                if (not is_long) and high[j] >= lim_price:
                    entry, esc = lim_price, j
                    break
            if entry is None or esc is None:
                i += 1
                continue
            filled += 1
            e_i = esc
        else:
            entry = close[i]
            e_i = i
        tp_pct, sl_pct = float(tp_arr[e_i]) if limit_fill else float(tp_arr[i]), \
                         float(sl_arr[e_i]) if limit_fill else float(sl_arr[i])
        if tp_pct <= 0 or sl_pct <= 0:
            i += 1
            continue
        if is_long:
            sl_price = entry * (1 - sl_pct)
            tp_price = entry * (1 + tp_pct)
        else:
            sl_price = entry * (1 + sl_pct)
            tp_price = entry * (1 - tp_pct)
        exit_i = None
        exit_reason = None
        exit_price = None
        for j in range(e_i + 1, min(e_i + 1 + max_hold, n)):
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
            exit_i = min(e_i + max_hold, n - 1)
            exit_reason = "TIMEOUT"
            exit_price = close[exit_i]
        ret = (exit_price / entry - 1.0) if is_long else (entry / exit_price - 1.0)
        ret -= cost
        trades.append(dict(reason=exit_reason, ret=ret))
        i = exit_i + cooldown
    return trades, signals, filled


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
    return dict(n=n, wr=float((r > 0).mean()), avg=float(r.mean()),
                t=tstat, tp=reasons.get("TP", 0) / n, sl=reasons.get("SL", 0) / n,
                to=reasons.get("TIMEOUT", 0) / n)


def run():
    for variant in ["V0_市价", "V1_限价", "V2_EMA强过滤", "V3_RSI35", "V4_振幅上界6%"]:
        all_tr = []
        sig = 0
        fills = 0
        per_sym = {}
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
            amp_hi = 0.06 if variant == "V4_振幅上界6%" else AMP_HI
            amp_ok = (amp >= AMP_LO) & (amp <= amp_hi)
            valid = (~np.isnan(rp)) & (~np.isnan(rsi_v)) & (~np.isnan(amp)) & gap_ok & amp_ok
            rsi_lo = 35.0 if variant == "V3_RSI35" else RSI_OS
            rsi_hi = 65.0 if variant == "V3_RSI35" else RSI_OB
            if variant == "V2_EMA强过滤":
                trend_l = (ema50 > ema200) & (close > ema200)
                trend_s = (ema50 < ema200) & (close < ema200)
            else:
                trend_l = (close > sma100) & (sma20 > sma100)
                trend_s = (close < sma100) & (sma20 < sma100)
            lm_ = valid & trend_l & (rp <= LOW_BAND) & (rsi_v <= rsi_lo)
            sm_ = valid & trend_s & (rp >= HIGH_BAND) & (rsi_v >= rsi_hi)
            tp_arr, sl_arr = mr_tp_sl(close, hi_win, lo_win, rp)
            use_limit = (variant == "V1_限价")
            cost = LIMIT_COST if use_limit else BASE_COST
            tr, s_, f_ = sim(df, lm_, sm_, tp_arr, sl_arr, cost,
                             limit_fill="edge" if use_limit else None,
                             lo_win_arr=lo_win, hi_win_arr=hi_win)
            st = agg(tr)
            if st:
                st["sym"] = sym
                per_sym[sym] = st
                all_tr.extend(tr)
            sig += s_
            fills += f_
        st = agg(all_tr)
        if not st:
            print(f"{variant}: 无交易")
            continue
        half = len(all_tr) // 2
        h1 = agg(all_tr[:half])
        h2 = agg(all_tr[half:])
        print(f"\n{variant:<14} n={st['n']:>5} 胜率={st['wr']*100:5.2f}% 净={st['avg']*100:+.3f}%/笔 "
              f"t={st['t']:+.2f} TP={st['tp']*100:4.1f}% SL={st['sl']*100:4.1f}% TO={st['to']*100:4.1f}% "
              f"前50%={h1['avg']*100:+.3f}% 后50%={h2['avg']*100:+.3f}% 信号{sig} 成交{fills} ({100*fills/max(sig,1):.0f}%)")
        top = sorted(per_sym.items(), key=lambda kv: -kv[1]["avg"])
        print("  各币净(%): " + "  ".join(f"{k}:{v['avg']*100:+.2f}({v['n']})" for k, v in top))


if __name__ == "__main__":
    run()
