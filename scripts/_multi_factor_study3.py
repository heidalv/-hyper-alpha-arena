# -*- coding: utf-8 -*-
"""多因子策略研究③：生产语义忠实回测（5m, 48根窗口, 真实 TP/SL/超时机制）。

完全复刻 backend/services/scalp/scalp_ranging_mr.py 的选点与拆单：
  - 窗口：last 48×5m bars（4h）→ swing_low/high（high.max/low.min）
  - range_pos = (close-swing_low)/(swing_high-swing_low)
  - 多头：range_pos <= 0.30 且 rsi14 <= 40；空头：range_pos >= 0.70 且 rsi14 >= 60
  - 振幅 (swing_high-swing_low)/price ∈ [1.5%, 5.0%]
  - TP = dist_to_far × 0.55；SL = dist_to_edge + 0.8% 缓冲；SL clip [0.6%,3.0%]；
    min TP 0.6%；RR 一致性：sl ≤ tp/min_rr(1.0)，不够则抬 tp ≤ 4%
  - 执行：信号 bar 收盘入场（close[i]），持仓 ≤ 24 根(2h)，超时按收盘平仓；
    同 bar 先碰 SL 记 SL（保守）；成本 9bps 往返；同币一仓（信号去重+12根冷却）
  - 输出：各变体 净/胜率/盈亏比/平均持仓/TIMEOUT占比/TP占比/SL占比，
    以及 head-of-trade MFE 分布（诊断退出是"够不到"还是"拿不住"）
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
WIN = 48          # 48×5m = 4h
MAXHOLD = 24      # 2h
COOLDOWN = 12     # 冷却（5m）

# 生产参数
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


def sim(df, entry_masks_long, entry_masks_short, tp_arr, sl_arr, dedup=True, max_hold=MAXHOLD):
    """单币模拟。entry mask 为 bool 数组（True=该 bar 收盘发出信号）。
    返回每个已成交 trade 的 dict。"""
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    trades = []
    i = 0
    while i < n - 1:
        if not (entry_masks_long[i] or entry_masks_short[i]):
            i += 1
            continue
        is_long = bool(entry_masks_long[i]) and not bool(entry_masks_short[i])
        if not is_long and not entry_masks_short[i]:
            i += 1
            continue
        # 终止信号（生产结构参数）；实际拆单时 TP/SL 已算好
        entry = close[i]
        # 按生产公式计算 tp/sl_pct（需要 swing）
        # 这里直接使用预设的 per-bar tp/sl 数组
        tp_pct = tp_arr[i]
        sl_pct = sl_arr[i]
        if tp_pct <= 0 or sl_pct <= 0:
            i += 1
            continue
        if is_long:
            sl_price = entry * (1 - sl_pct)
            tp_price = entry * (1 + tp_pct)
        else:
            sl_price = entry * (1 + sl_pct)
            tp_price = entry * (1 - tp_pct)
        # 已持仓 → 15 分钟后再允许信号去重（由 dedup 外部控制）+ 冷却
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
        if is_long:
            ret = exit_price / entry - 1.0
        else:
            ret = entry / exit_price - 1.0
        ret -= COST
        trades.append(dict(entry_i=i, exit_i=exit_i, reason=exit_reason, ret=ret,
                           tp_pct=tp_pct, sl_pct=sl_pct, hold=(exit_i - i)))
        i = exit_i + COOLDOWN if dedup else i + 1
    return trades


def summarize(trades, label):
    if not trades:
        return dict(label=label, n=0)
    r = np.array([t["ret"] for t in trades])
    wr = float((r > 0).mean())
    avg = float(r.mean())
    tot = float(r.sum())
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    tpsd = np.array([t["tp_pct"] for t in trades])
    slsd = np.array([t["sl_pct"] for t in trades])
    holds = np.array([t["hold"] for t in trades])
    # MFE 诊断：入场后 max_hold 根内最大有利/不利移动
    return dict(label=label, n=len(trades), wr=wr, avg=avg, tot=tot,
                reason_pct={k: v / len(trades) for k, v in reasons.items()},
                tp_mean=float(tpsd.mean()), sl_mean=float(slsd.mean()),
                hold_mean=float(holds.mean()),
                avg_win=float(r[r > 0].mean()) if (r > 0).any() else 0.0,
                avg_loss=float(r[r <= 0].mean()) if (r <= 0).any() else 0.0)


def run():
    results = []
    for sym in SYMBOLS:
        df, gap_ok = load(sym)
        close = df["close"].to_numpy()
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        n = len(df)

        hi_win = pd.Series(high).rolling(WIN).max().to_numpy()
        lo_win = pd.Series(low).rolling(WIN).min().to_numpy()
        rng = np.where(hi_win - lo_win > 1e-12, hi_win - lo_win, np.nan)
        rp = np.where(np.isnan(rng), np.nan, (close - lo_win) / rng)
        rsi_v = rsi(close, 14)
        z20 = zscore(close, 20)
        zvol = zscore(df["vol"].to_numpy(), 20)
        mom6 = np.concatenate([[np.nan] * 6, close[6:] / close[:-6] - 1.0])

        amp = np.where(np.isnan(rng), np.nan, rng / close)
        amp_ok = (amp >= AMP_LO) & (amp <= AMP_HI)
        valid = (~np.isnan(rp)) & (~np.isnan(rsi_v)) & (~np.isnan(amp)) & gap_ok & amp_ok

        # 生产 TP/SL 逐 bar 计算
        dist_far = np.where(rp > 0.5, rng - (close - lo_win), close - lo_win)  # 到远沿
        # 简化：远沿 = min(distance to swing_high, distance to swing_low) 取决于方向
        tp_arr = np.full(n, np.nan)
        sl_arr = np.full(n, np.nan)
        for i in range(n):
            if np.isnan(rp[i]):
                continue
            if rp[i] <= LOW_BAND:
                dist_to_far = max(hi_win[i] - close[i], 0.0)
                tpx = max(dist_to_far * TP_FRAC / close[i], MIN_TP)
                slx = max((close[i] - lo_win[i]) / close[i] + EDGE_BUF, SL_FLOOR)
            elif rp[i] >= HIGH_BAND:
                dist_to_far = max(close[i] - lo_win[i], 0.0)
                tpx = max(dist_to_far * TP_FRAC / close[i], MIN_TP)
                slx = max((hi_win[i] - close[i]) / close[i] + EDGE_BUF, SL_FLOOR)
            else:
                continue
            slx = min(slx, SL_CAP)
            # RR 一致性
            cap_for_rr = tpx / MIN_RR
            if cap_for_rr >= SL_FLOOR:
                slx = min(slx, cap_for_rr)
            elif slx > 0 and (tpx / slx) < MIN_RR:
                tpx = min(0.04, max(tpx, slx * MIN_RR))
            tp_arr[i], sl_arr[i] = tpx, slx

        base_long = valid & (rp <= LOW_BAND) & (rsi_v <= RSI_OS)
        base_short = valid & (rp >= HIGH_BAND) & (rsi_v >= RSI_OB)

        variants = {
            "生产基线": (base_long, base_short),
            "+z20确认": (base_long & (z20 < -1.0), base_short & (z20 > 1.0)),
            "+放量确认": (base_long & (zvol > 0.6), base_short & (zvol > 0.6)),
            "+动量确认": (base_long & (mom6 < -0.004), base_short & (mom6 > 0.004)),
            "三选二": (
                base_long & ((z20 < -1.0).astype(int) + (zvol > 0.6).astype(int) + (mom6 < -0.004).astype(int) >= 2),
                base_short & ((z20 > 1.0).astype(int) + (zvol > 0.6).astype(int) + (mom6 > 0.004).astype(int) >= 2),
            ),
            "紧边0.25/0.75": (valid & (rp <= 0.25) & (rsi_v <= 40), valid & (rp >= 0.75) & (rsi_v >= 60)),
            "极RSI 30/70": (valid & (rp <= 0.30) & (rsi_v <= 30), valid & (rp >= 0.70) & (rsi_v >= 70)),
        }
        for name, (lm, sm) in variants.items():
            trades = sim(df, lm, sm, tp_arr, sl_arr)
            st = summarize(trades, f"{sym}/{name}")
            st["sym"] = sym
            st["name"] = name
            results.append(st)

    # 汇总
    from collections import defaultdict
    agg = defaultdict(list)
    for r_ in results:
        agg[r_["name"]].append(r_)
    print(f"\n===== 生产语义忠实回测（{PERIOD} {EXCHANGE} {len(SYMBOLS)}币, cost={COST*1e4:.0f}bps, maxhold={MAXHOLD}根==2h） =====")
    print(f"{'变体':<14}{'n':>6} {'胜率':>7} {'单笔净':>8} {'TP占比':>7} {'SL占比':>7} {'超时':>7} {'avg胜':>8} {'avg亏':>8}")
    ranked = []
    for name, subs in agg.items():
        n = sum(s["n"] for s in subs)
        if n == 0:
            continue
        w = sum(s["n"] for s in subs)
        awr = sum(s["wr"] * s["n"] for s in subs) / w
        aavg = sum(s["avg"] * s["n"] for s in subs) / w
        tp_share = sum(s["reason_pct"].get("TP", 0) * s["n"] for s in subs) / w
        sl_share = sum(s["reason_pct"].get("SL", 0) * s["n"] for s in subs) / w
        to_share = sum(s["reason_pct"].get("TIMEOUT", 0) * s["n"] for s in subs) / w
        wins = sum(max(s["avg_win"], 0) * s["n"] for s in subs)
        losses = sum(min(s["avg_loss"], 0) * s["n"] for s in subs)
        awin = wins / sum(s["n"] for s in subs)
        aloss = losses / sum(s["n"] for s in subs)
        ranked.append((name, n, awr, aavg, tp_share, sl_share, to_share, awin, aloss))
    ranked.sort(key=lambda x: -x[3])
    for name, n, awr, aavg, tp, sl, to, awin, aloss in ranked:
        print(f"  {name:<14}{n:>6} {awr*100:>6.2f}% {aavg*100:>+7.3f}% {tp*100:>6.1f}% {sl*100:>6.1f}% {to*100:>6.1f}% {awin*100:>+7.3f}% {aloss*100:>+7.3f}%")

    print("\n  各币 x 生产基线 模拟明细:")
    for s in sorted(results, key=lambda x: x["sym"]):
        if s["name"] != "生产基线":
            continue
        print(f"    {s['sym']:<5} n={s['n']:>4} wr={s['wr']*100:5.1f}% avg={s['avg']*100:+.3f}% "
              f"TP={s['reason_pct'].get('TP',0)*100:4.1f}% SL={s['reason_pct'].get('SL',0)*100:4.1f}% TO={s['reason_pct'].get('TIMEOUT',0)*100:4.1f}% "
              f"win={s['avg_win']*100:+.3f}% loss={s['avg_loss']*100:+.3f}% hold={s['hold_mean']:.1f}根")


if __name__ == "__main__":
    run()
