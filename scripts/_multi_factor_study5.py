# -*- coding: utf-8 -*-
"""多因子策略研究⑤：时间框架扫描 + 退出侧扫描（同一选点族，逐 TF 参数适配）。

研究③④结论：5m 上 MR 族全负（-0.05%）、突破族更差（-0.12%）；pre-cost MR 有 ≈+3bp
但 9bp 成本吃光 → 核心问题可能是「成本 vs 波幅」的比率随 TF 变化。

本脚本扫描：
T1. TF ∈ {5m, 15m, 1h} × 选点族 {MR边缘+RSI极端(生产口径), 趋势回调, 纯RSI极端}，
    退出=生产贴边 TP/SL（逐 TF 用生产公式），成本 9bps，maxhold = 24 根。
T2. 退出侧扫描（5m MR 基线 + 15m MR）：
    TP_FRAC ∈ {0.35, 0.45, 0.55} × maxhold ∈ {12, 24} × 入场 sig 延迟 ∈ {0(收盘),1(下一根开盘)}
    —— 诊断「够不到 TP → 超时白交成本」是否可以靠更近 TP 或更短持仓救回。
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "ADA", "ARB"]
EXCHANGE = "binance"
N_BARS = 20000
COST = 0.0009

LOW_BAND, HIGH_BAND = 0.30, 0.70
RSI_OS, RSI_OB = 40.0, 60.0
AMP_LO, AMP_HI = 0.015, 0.050
TP_FRAC = 0.55
EDGE_BUF = 0.008
MIN_TP = 0.006
SL_FLOOR, SL_CAP = 0.006, 0.030
MIN_RR = 1.0

PERIOD_SEC = {"5m": 300, "15m": 900, "1h": 3600}


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    up = np.maximum(delta, 0.0)
    dn = np.maximum(-delta, 0.0)
    ru = pd.Series(up).rolling(n).mean().to_numpy()
    rd = pd.Series(dn).rolling(n).mean().to_numpy()
    out = 100.0 - 100.0 / (1.0 + ru / np.maximum(rd, 1e-12))
    return np.nan_to_num(out, nan=50.0)


def load(sym: str, period: str):
    from backend.database.connection import MarketSessionLocal  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    with MarketSessionLocal() as db:
        rows = db.execute(text("""
            SELECT timestamp, open_price, high_price, low_price, close_price, volume
            FROM crypto_klines
            WHERE exchange = :ex AND symbol = :sym AND period = :per
              AND high_price IS NOT NULL AND low_price IS NOT NULL
            ORDER BY timestamp DESC LIMIT :lim
        """), {"ex": EXCHANGE, "sym": sym.upper(), "per": period, "lim": N_BARS}).fetchall()
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
    gap_ok[1:] = np.diff(df["ts"].to_numpy()) <= PERIOD_SEC[period] * 1.5
    return df, gap_ok


def sim(df, lm, sm, tp_arr, sl_arr, max_hold, cooldown=12, entry_delay=0):
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
        e_i = i + entry_delay
        if e_i >= n - 1:
            i += 1
            continue
        if np.isnan(tp_arr[i]) or np.isnan(sl_arr[i]) or tp_arr[i] <= 0 or sl_arr[i] <= 0:
            i += 1
            continue
        entry = close[e_i]
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
        ret -= COST
        trades.append(dict(reason=exit_reason, ret=ret))
        i = exit_i + cooldown
    return trades


def agg(trades):
    if not trades:
        return None
    r = np.array([t["ret"] for t in trades])
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return dict(n=len(trades), wr=float((r > 0).mean()), avg=float(r.mean()),
                tp=reasons.get("TP", 0) / len(trades),
                sl=reasons.get("SL", 0) / len(trades),
                to=reasons.get("TIMEOUT", 0) / len(trades))


def mr_tp_sl(close, high, low, hi_win, lo_win, rp, tp_frac=TP_FRAC):
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


def run():
    # T1: TF 扫描
    tf_conf = {"5m": 24, "15m": 24, "1h": 24}
    t1_rows = []
    for period, maxhold in tf_conf.items():
        for sym in SYMBOLS:
            ld = load(sym, period)
            if ld is None:
                continue
            df, gap_ok = ld
            close = df["close"].to_numpy()
            high = df["high"].to_numpy()
            low = df["low"].to_numpy()
            vol = df["vol"].to_numpy()
            n = len(df)
            hi_win = pd.Series(high).rolling(48).max().to_numpy()
            lo_win = pd.Series(low).rolling(48).min().to_numpy()
            rng = np.where(hi_win - lo_win > 1e-12, hi_win - lo_win, np.nan)
            rp = np.where(np.isnan(rng), np.nan, (close - lo_win) / rng)
            rsi_v = rsi(close, 14)
            sma20 = pd.Series(close).rolling(20).mean().to_numpy()
            sma100 = pd.Series(close).rolling(100).mean().to_numpy()
            amp = np.where(np.isnan(rng), np.nan, rng / close)
            amp_ok = (amp >= AMP_LO) & (amp <= AMP_HI)
            valid = (~np.isnan(rp)) & (~np.isnan(rsi_v)) & (~np.isnan(amp)) & gap_ok & amp_ok
            tp_arr, sl_arr = mr_tp_sl(close, high, low, hi_win, lo_win, rp)

            base = (valid & (rp <= LOW_BAND) & (rsi_v <= RSI_OS), valid & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB))
            pull = (valid & (close > sma100) & (sma20 > sma100) & (rp <= LOW_BAND) & (rsi_v <= RSI_OS),
                    valid & (close < sma100) & (sma20 < sma100) & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB))
            ext = (valid & (rsi_v <= 30) & (rp <= 0.30), valid & (rsi_v >= 70) & (rp >= 0.70))

            for fam, (lm_, sm_) in [("MR基线", base), ("趋势回调", pull), ("极端RSI", ext)]:
                tr = sim(df, lm_, sm_, tp_arr, sl_arr, maxhold)
                st = agg(tr)
                if st:
                    st.update(period=period, sym=sym, fam=fam)
                    t1_rows.append(st)

    print(f"\n===== T1 时间框架扫描（cost={COST*1e4:.0f}bps, 48根摆动窗口, maxhold=24根） =====")
    for fam in ["MR基线", "趋势回调", "极端RSI"]:
        print(f"\n  [{fam}]")
        for period in ["5m", "15m", "1h"]:
            subs = [r_ for r_ in t1_rows if r_["fam"] == fam and r_["period"] == period]
            if not subs:
                print(f"    {period:<5} 无数据")
                continue
            w = sum(s["n"] for s in subs)
            awr = sum(s["wr"] * s["n"] for s in subs) / w
            aavg = sum(s["avg"] * s["n"] for s in subs) / w
            tp = sum(s["tp"] * s["n"] for s in subs) / w
            sl = sum(s["sl"] * s["n"] for s in subs) / w
            to = sum(s["to"] * s["n"] for s in subs) / w
            print(f"    {period:<5} n={w:>5} 胜率={awr*100:5.2f}% 单笔净={aavg*100:+.3f}% TP={tp*100:5.1f}% SL={sl*100:5.1f}% TO={to*100:5.1f}%")

    # T2: 退出侧扫描（5m/15m MR 基线）
    print("\n===== T2 退出侧扫描（MR基线, TP_FRAC × maxhold × entry延迟, 跨币合并） =====")
    for period, maxhold_default in [("5m", 24), ("15m", 24)]:
        for tp_frac in (0.35, 0.45, 0.55):
            for maxhold in (12, 24):
                for entry_delay in (0,):
                    all_tr = []
                    for sym in SYMBOLS:
                        ld = load(sym, period)
                        if ld is None:
                            continue
                        df, gap_ok = ld
                        close = df["close"].to_numpy()
                        high = df["high"].to_numpy()
                        low = df["low"].to_numpy()
                        n = len(df)
                        hi_win = pd.Series(high).rolling(48).max().to_numpy()
                        lo_win = pd.Series(low).rolling(48).min().to_numpy()
                        rng = np.where(hi_win - lo_win > 1e-12, hi_win - lo_win, np.nan)
                        rp = np.where(np.isnan(rng), np.nan, (close - lo_win) / rng)
                        rsi_v = rsi(close, 14)
                        amp = np.where(np.isnan(rng), np.nan, rng / close)
                        amp_ok = (amp >= AMP_LO) & (amp <= AMP_HI)
                        valid = (~np.isnan(rp)) & (~np.isnan(rsi_v)) & (~np.isnan(amp)) & gap_ok & amp_ok
                        tp_arr, sl_arr = mr_tp_sl(close, high, low, hi_win, lo_win, rp, tp_frac)
                        lm_ = valid & (rp <= LOW_BAND) & (rsi_v <= RSI_OS)
                        sm_ = valid & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB)
                        all_tr.extend(sim(df, lm_, sm_, tp_arr, sl_arr, maxhold, entry_delay=entry_delay))
                    st = agg(all_tr)
                    if st:
                        print(f"    {period:<4} TP_FRAC={tp_frac:.2f} maxhold={maxhold:<3} delay={entry_delay} "
                              f"n={st['n']:>5} 胜率={st['wr']*100:5.2f}% 单笔净={st['avg']*100:+.3f}% "
                              f"TP={st['tp']*100:5.1f}% SL={st['sl']*100:5.1f}% TO={st['to']*100:5.1f}%")


if __name__ == "__main__":
    run()
