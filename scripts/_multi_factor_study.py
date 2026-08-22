# -*- coding: utf-8 -*-
"""多因子策略研究①：条件触发型多因子组合（MR 口径） vs RSI-only。

设计（与 scalp_ranging_mr 执行语义一致）：
  - 行情：15m BTC/ETH/SOL，近 2500 根
  - 条件触发：区间位置 range_pos（16 根 swing 窗口,≈4h）到达边缘 + 确认因子
  - 因子候选：rsi14 / z20 反转 / vol_z / mom6 反转 / atr_pct
  - 评价：next open 入场，forward 6 根 (1.5h)，成本 9bps 往返
  - 输出：各单因子条件胜率+净收益、组合加权、与 RSI-only 基线对比
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

from backend.services.kline_data_service import kline_service  # noqa: E402

SYMBOLS = ["BTC", "ETH", "SOL"]
PERIOD = "15m"
N_BARS = 2500
FWD = 6          # 6×15m = 1.5h
COST = 0.0009     # 9bps 往返
RANGE_WIN = 16    # 16×15m = 4h 区间窗口（≈ 原 MR 48×5m）


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


def load_klines(sym: str):
    rows = kline_service.get_klines_from_db(
        sym, PERIOD, count=N_BARS,
        exchange=os.getenv("STUDY_KLINE_EXCHANGE", "") or None,
    )
    if not rows or len(rows) < 500:
        raise RuntimeError(f"no klines {sym}: {len(rows) if rows else 0}")
    df = pd.DataFrame(rows)
    close = df["close"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else np.ones(len(close))
    return close, vol


def run():
    all_rows = []
    for sym in SYMBOLS:
        close, volume = load_klines(sym)
        n = len(close)
        hi = pd.Series(close).rolling(RANGE_WIN).max().to_numpy()
        lo = pd.Series(close).rolling(RANGE_WIN).min().to_numpy()
        rng = np.where(hi - lo > 1e-12, hi - lo, np.nan)
        range_pos = np.where(np.isnan(rng), np.nan, (close - lo) / rng)
        rsi_v = rsi(close, 14)
        z20_v = zscore(close, 20)
        zvol = zscore(volume, 20)
        mom6 = np.concatenate([[np.nan] * 6, close[6:] / close[:-6] - 1.0])
        atr_pct = np.abs(np.diff(close, prepend=close[0])) / close

        # forward next-open 收益（信号 bar 的下一根开盘买入，+FWD 根后开盘卖出）
        fwd_ret = np.full(n, np.nan)
        for i in range(n - FWD - 1):
            fwd_ret[i] = close[i + 1 + FWD] / close[i + 1] - 1.0

        def cond_mask(pos_lo, pos_hi, rsi_lo, rsi_hi):
            return (
                (~np.isnan(range_pos)) & (~np.isnan(rsi_v)) & (~np.isnan(fwd_ret))
                & (rng > 0.0)
                & ((range_pos <= pos_lo) | (range_pos >= pos_hi))
            )

        base_long = cond_mask(0.20, np.inf, -1, 40.0)   # 低位 + RSI≤40
        base_short = cond_mask(-np.inf, 0.80, 60.0, 999)  # 高位 + RSI≥60

        def stats(mask, sign):
            if mask.sum() < 20:
                return None
            ret = fwd_ret[mask] * sign - COST
            wr = float((ret > 0).mean())
            return dict(n=int(mask.sum()), wr=round(wr, 4),
                        avg=round(float(ret.mean()), 6),
                        net=round(float(ret.sum()), 4))

        # 确认组合（都基于"边缘+RSI极端"触发条件，叠加额外确认因子）
        m_rsionly = base_long | base_short
        m_z20 = (base_long & (z20_v < -1.0)) | (base_short & (z20_v > 1.0))
        m_vol = (base_long & (zvol > 0.6)) | (base_short & (zvol > 0.6))
        m_mom = (base_long & (mom6 < -0.004)) | (base_short & (mom6 > 0.004))
        m_multi = (base_long & (z20_v < -0.8) & (zvol > 0.5) & (mom6 < -0.002)) | \
                  (base_short & (z20_v > 0.8) & (zvol > 0.5) & (mom6 > 0.002))
        for name, mask in [
            ("RSI-only(基线)", m_rsionly),
            ("+z20反转确认", m_z20),
            ("+量能放大确认", m_vol),
            ("+动量反转确认", m_mom),
            ("三位一体组合", m_multi),
        ]:
            st = stats(mask, 1.0)
            if st:
                all_rows.append((sym, name, st))

    df = pd.DataFrame(all_rows, columns=["sym", "combo", "stats"])
    print("\n===== 多因子条件信号研究（15m, fwd=6, cost=9bps） =====")
    for combo in df["combo"].unique():
        sub = df[df["combo"] == combo]
        tot_n = int(sub["stats"].apply(lambda x: x["n"]).sum())
        avg_wr = float(np.average(sub["stats"].apply(lambda x: x["wr"]), weights=sub["stats"].apply(lambda x: x["n"])))
        avg_ret = float(np.average(sub["stats"].apply(lambda x: x["avg"]), weights=sub["stats"].apply(lambda x: x["n"])))
        print(f"  {combo:<18} n={tot_n:>5} 胜率={avg_wr*100:5.2f}% 单笔净={avg_ret*100:+.3f}%")
    print("\n  各币明细:")
    for _, r in df.iterrows():
        st = r["stats"]
        print(f"    {r['sym']:<4} {r['combo']:<18} n={st['n']:>4} wr={st['wr']*100:5.2f}% avg={st['avg']*100:+.3f}% net={st['net']:+.2f}")


if __name__ == "__main__":
    run()
