# -*- coding: utf-8 -*-
"""多因子策略研究④：假设族对比（同一 5m 数据、同一执行框架）。

研究③结论：生产 MR 选点 ≈ 无边缘（49.9% WR、对称盈亏、47.5% 超时）；
多因子确认（z20/vol/mom/三选二/极端RSI）均不能救活。→ 换假设族：

A. 趋势回调族（"低吸在上升趋势中"）：
   做多 = 区间低位 + RSI≤40 + 斜率过滤(价>SMA100 且 SMA20>SMA100)
   做空 = 区间高位 + RSI≥60 + 斜率过滤(价<SMA100 且 SMA20<SMA100)
   退出沿用 MR 贴边 TP/SL（可与生产对比）
B. 动量突破族：
   做多 = 突破 48 根高点（close > 前 47 根 high.max，1 根内）且 vol ≥ 1.2×avg20 且 RSI≥55
   做空 = 镜像
   退出：固定 TP=1.0% / SL=0.6% / RR1.67，max hold 24 根
C. 动量突破+多因子确认（z20>1 / vol 强化 / 近端动量同向）三选二
输出：各变体净/胜率/TP/SL/超时占比 + 前后 50% 稳定性 + 各币明细
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "ADA", "ARB"]
PERIOD = "5m"
EXCHANGE = "binance"
N_BARS = 20000
COST = 0.0009
WIN = 48
MAXHOLD = 24
COOLDOWN = 12

# MR 生产参数（A 族沿用）
LOW_BAND, HIGH_BAND = 0.30, 0.70
RSI_OS, RSI_OB = 40.0, 60.0
AMP_LO, AMP_HI = 0.015, 0.050
TP_FRAC = 0.55
EDGE_BUF = 0.008
MIN_TP = 0.006
SL_FLOOR, SL_CAP = 0.006, 0.030
MIN_RR = 1.0

# B/C 族参数
MOM_TP = 0.010
MOM_SL = 0.006
VOL_RATIO = 1.2


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    up = np.maximum(delta, 0.0)
    dn = np.maximum(-delta, 0.0)
    ru = pd.Series(up).rolling(n).mean().to_numpy()
    rd = pd.Series(dn).rolling(n).mean().to_numpy()
    out = 100.0 - 100.0 / (1.0 + ru / np.maximum(rd, 1e-12))
    return np.nan_to_num(out, nan=50.0)


def zscore(x: np.ndarray, n: int = 20) -> np.ndarray:
    s = pd.Series(x)
    m = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return ((s - m) / sd.replace(0, np.nan)).to_numpy()


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
    if not rows or len(rows) < 6000:
        raise RuntimeError(f"no klines {sym}: {len(rows) if rows else 0}")
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
    gap_ok[1:] = np.diff(df["ts"].to_numpy()) <= 300 * 1.5
    return df, gap_ok


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
        entry = close[i]
        if np.isnan(tp_arr[i]) or np.isnan(sl_arr[i]) or tp_arr[i] <= 0 or sl_arr[i] <= 0:
            i += 1
            continue
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
        trades.append(dict(entry_i=i, exit_i=exit_i, reason=exit_reason, ret=ret,
                           tp_pct=tp_pct, sl_pct=sl_pct, hold=(exit_i - i)))
        i = exit_i + cooldown
    return trades


def agg_stats(trades):
    if not trades:
        return None
    r = np.array([t["ret"] for t in trades])
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    wins = r[r > 0]
    losses = r[r <= 0]
    return dict(n=len(trades), wr=float((r > 0).mean()), avg=float(r.mean()),
                tot=float(r.sum()), tp=reasons.get("TP", 0) / len(trades),
                sl=reasons.get("SL", 0) / len(trades),
                to=reasons.get("TIMEOUT", 0) / len(trades),
                awin=float(wins.mean()) if wins.size else 0.0,
                aloss=float(losses.mean()) if losses.size else 0.0,
                hold=float(np.mean([t["hold"] for t in trades])))


def run():
    per_combo = {}   # name -> list of stat dicts per symbol
    per_symbol = {s: {} for s in SYMBOLS}
    for sym in SYMBOLS:
        df, gap_ok = load(sym)
        close = df["close"].to_numpy()
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        vol = df["vol"].to_numpy()
        n = len(df)

        hi_win = pd.Series(high).rolling(WIN).max().to_numpy()
        lo_win = pd.Series(low).rolling(WIN).min().to_numpy()
        # 突破判定：前 47 根 high.max（不含当前根）
        hi_prev = pd.Series(high).rolling(WIN).max().shift(1).to_numpy()
        lo_prev = pd.Series(low).rolling(WIN).min().shift(1).to_numpy()
        rng = np.where(hi_win - lo_win > 1e-12, hi_win - lo_win, np.nan)
        rp = np.where(np.isnan(rng), np.nan, (close - lo_win) / rng)
        rsi_v = rsi(close, 14)
        z20 = zscore(close, 20)
        zvol = zscore(vol, 20)
        mom6 = np.concatenate([[np.nan] * 6, close[6:] / close[:-6] - 1.0])
        vol_ratio = vol / np.maximum(pd.Series(vol).rolling(20).mean().to_numpy(), 1e-12)
        sma20 = pd.Series(close).rolling(20).mean().to_numpy()
        sma100 = pd.Series(close).rolling(100).mean().to_numpy()
        amp = np.where(np.isnan(rng), np.nan, rng / close)
        amp_ok = (amp >= AMP_LO) & (amp <= AMP_HI)
        valid = (~np.isnan(rp)) & (~np.isnan(rsi_v)) & (~np.isnan(amp)) & gap_ok & amp_ok

        # ── A 族：趋势回调（沿用 MR 贴边 TP/SL 套件） ──
        tp_arr_mr = np.full(n, np.nan)
        sl_arr_mr = np.full(n, np.nan)
        for i in range(n):
            if np.isnan(rp[i]):
                continue
            if rp[i] <= LOW_BAND:
                dist_far = max(hi_win[i] - close[i], 0.0)
                tpx = max(dist_far * TP_FRAC / close[i], MIN_TP)
                slx = max((close[i] - lo_win[i]) / close[i] + EDGE_BUF, SL_FLOOR)
            elif rp[i] >= HIGH_BAND:
                dist_far = max(close[i] - lo_win[i], 0.0)
                tpx = max(dist_far * TP_FRAC / close[i], MIN_TP)
                slx = max((hi_win[i] - close[i]) / close[i] + EDGE_BUF, SL_FLOOR)
            else:
                continue
            slx = min(slx, SL_CAP)
            cap_for_rr = tpx / MIN_RR
            if cap_for_rr >= SL_FLOOR:
                slx = min(slx, cap_for_rr)
            elif slx > 0 and (tpx / slx) < MIN_RR:
                tpx = min(0.04, max(tpx, slx * MIN_RR))
            tp_arr_mr[i], sl_arr_mr[i] = tpx, slx

        up_slope = (close > sma100) & (sma20 > sma100)
        dn_slope = (close < sma100) & (sma20 < sma100)
        valid_sl = (~np.isnan(sma100)) & gap_ok

        a_base = (valid & (rp <= LOW_BAND) & (rsi_v <= RSI_OS), valid & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB))
        a_pull = (valid_sl & up_slope & (rp <= LOW_BAND) & (rsi_v <= RSI_OS),
                  valid_sl & dn_slope & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB))

        # ── B 族：动量突破（固定 TP/SL） ──
        b_brk = ((~np.isnan(hi_prev)) & gap_ok & (close > hi_prev) & (vol_ratio >= VOL_RATIO) & (rsi_v >= 55),
                 (~np.isnan(lo_prev)) & gap_ok & (close < lo_prev) & (vol_ratio >= VOL_RATIO) & (rsi_v <= 45))
        # C 族：突破 + 三选二确认（z20、放量 zvol>0.5、近端动量同向）
        b_cf1 = (z20 > 1.0, z20 < -1.0)
        b_cf2 = (zvol > 0.5, zvol > 0.5)
        b_cf3 = (mom6 > 0.002, mom6 < -0.002)
        b_major = (
            b_brk[0] & (b_cf1[0].astype(int) + b_cf2[0].astype(int) + b_cf3[0].astype(int) >= 2),
            b_brk[1] & (b_cf1[1].astype(int) + b_cf2[1].astype(int) + b_cf3[1].astype(int) >= 2),
        )

        tp_arr_mom = np.where(np.isfinite(close), MOM_TP, np.nan)
        sl_arr_mom = np.where(np.isfinite(close), MOM_SL, np.nan)

        variants = {
            "MR基线(对照)": (a_base, tp_arr_mr, sl_arr_mr),
            "A 趋势回调MR": (a_pull, tp_arr_mr, sl_arr_mr),
            "B 动量突破": (b_brk, tp_arr_mom, sl_arr_mom),
            "C 突破+3选2": (b_major, tp_arr_mom, sl_arr_mom),
        }
        for name, ((lm_, sm_), tp_a, sl_a) in variants.items():
            tr = sim(df, lm_, sm_, tp_a, sl_a)
            st = agg_stats(tr)
            if st:
                st["sym"] = sym
                st["name"] = name
                per_combo.setdefault(name, []).append(st)
                per_symbol[sym][name] = st
                # 前后 50% 稳定性
                half = len(tr) // 2
                st_h1 = agg_stats(tr[:half])
                st_h2 = agg_stats(tr[half:])
                st["h1"] = st_h1["avg"] if st_h1 else None
                st["h2"] = st_h2["avg"] if st_h2 else None

    print(f"\n===== 假设族对比（{PERIOD} {EXCHANGE} {len(SYMBOLS)}币, cost={COST*1e4:.0f}bps, maxhold={MAXHOLD}根） =====")
    print(f"{'变体':<14}{'n':>6} {'胜率':>7} {'单笔净':>8} {'TP':>6} {'SL':>6} {'TO':>6} {'avg胜':>8} {'avg亏':>8} {'前50%':>8} {'后50%':>8}")
    for name in ["MR基线(对照)", "A 趋势回调MR", "B 动量突破", "C 突破+3选2"]:
        subs = per_combo.get(name, [])
        if not subs:
            print(f"  {name:<14} 无交易")
            continue
        w = sum(s["n"] for s in subs)
        awr = sum(s["wr"] * s["n"] for s in subs) / w
        aavg = sum(s["avg"] * s["n"] for s in subs) / w
        tp = sum(s["tp"] * s["n"] for s in subs) / w
        sl = sum(s["sl"] * s["n"] for s in subs) / w
        to = sum(s["to"] * s["n"] for s in subs) / w
        awin = sum(s["awin"] * s["n"] for s in subs) / w
        aloss = sum(s["aloss"] * s["n"] for s in subs) / w
        h1s = [s["h1"] for s in subs if s.get("h1") is not None]
        h2s = [s["h2"] for s in subs if s.get("h2") is not None]
        h1 = sum(h1s) / len(h1s) if h1s else 0
        h2 = sum(h2s) / len(h2s) if h2s else 0
        print(f"  {name:<14}{w:>6} {awr*100:>6.2f}% {aavg*100:>+7.3f}% {tp*100:>5.1f}% {sl*100:>5.1f}% {to*100:>5.1f}% {awin*100:>+7.3f}% {aloss*100:>+7.3f}% {h1*100:>+7.3f}% {h2*100:>+7.3f}%")

    print("\n  各币 x 变体 单笔净(%):")
    for sym in SYMBOLS:
        parts = []
        for name in ["MR基线(对照)", "A 趋势回调MR", "B 动量突破", "C 突破+3选2"]:
            st = per_symbol[sym].get(name)
            if st:
                parts.append(f"{name}:{st['avg']*100:+.3f}")
        print(f"    {sym:<5} " + "  ".join(parts))


if __name__ == "__main__":
    run()
