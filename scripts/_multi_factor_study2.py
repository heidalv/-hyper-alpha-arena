# -*- coding: utf-8 -*-
"""多因子策略研究②：方向修正 + 提纯信号池 + 加权评分 vs 条件确认。

修复研究①的问题：
  - ①中空头信号按多头方向计算收益（sign=1），方向错误 → 本脚本按方向签名。
  - ①只用了 3 币 × 2500 根；本脚本用 7 币 × ≤8000 根（15m binance，≈86 天）。
  - ①只测条件确认；本脚本额外测「加权评分阈值」与「3 选 2 多数票」。

框架（与 scalp_ranging_mr 语义一致）：
  - 区间位置 range_pos（16×15m ≈ 4h）
  - 多头信号：range_pos ≤ 0.20 且 RSI ≤ 40；空头信号：range_pos ≥ 0.80 且 RSI ≥ 60
  - 收益：next open 入场，+FWD 根开盘平仓；多头 +fwd、空头 -fwd；成本 9bps 往返
  - 稳定性：每币 50/50 时间分割，看前后半段净收益符号是否一致
"""
import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.core.tenant import set_system_identity
set_system_identity()

from backend.services.kline_data_service import kline_service  # noqa: E402

SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "DOGE", "ADA", "ARB"]
PERIOD = "15m"
EXCHANGE = "binance"
N_BARS = 8000
FWD = 6          # 6×15m = 1.5h
COST = 0.0009    # 9bps 往返
RANGE_WIN = 16   # 16×15m = 4h 区间窗口
EDGE_LO, EDGE_HI = 0.20, 0.80
RSI_LO, RSI_HI = 40.0, 60.0


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
    """直查 alpha_market.crypto_klines（绕过数据中心的 trade 用途过期门槛，研究用途）。"""
    from backend.database.connection import MarketSessionLocal  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    with MarketSessionLocal() as db:
        rows = db.execute(text("""
            SELECT timestamp, open_price, close_price, volume
            FROM crypto_klines
            WHERE exchange = :ex AND symbol = :sym AND period = :per
              AND open_price IS NOT NULL AND close_price IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT :lim
        """), {"ex": EXCHANGE, "sym": sym.upper(), "per": PERIOD, "lim": N_BARS}).fetchall()
    if not rows or len(rows) < 2000:
        raise RuntimeError(f"no klines {sym}: {len(rows) if rows else 0}")
    rows = list(reversed(rows))
    close = np.array([r[2] for r in rows], dtype=float)
    vol = np.array([r[1] for r in rows], dtype=float)
    ts = np.array([r[0] for r in rows], dtype=np.int64)
    gap_ok = np.ones(len(ts), dtype=bool)
    gap_ok[1:] = np.diff(ts) <= 900 * 1.5
    return close, vol, gap_ok


def build_features(close, volume):
    n = len(close)
    hi = pd.Series(close).rolling(RANGE_WIN).max().to_numpy()
    lo = pd.Series(close).rolling(RANGE_WIN).min().to_numpy()
    rng = np.where(hi - lo > 1e-12, hi - lo, np.nan)
    range_pos = np.where(np.isnan(rng), np.nan, (close - lo) / rng)
    rsi_v = rsi(close, 14)
    z20_v = zscore(close, 20)
    zvol = zscore(volume, 20)
    mom6 = np.concatenate([[np.nan] * 6, close[6:] / close[:-6] - 1.0])
    fwd_ret = np.full(n, np.nan)
    for i in range(n - FWD - 1):
        fwd_ret[i] = close[i + 1 + FWD] / close[i + 1] - 1.0
    return dict(range_pos=range_pos, rsi=rsi_v, z20=z20_v, zvol=zvol,
                mom6=mom6, fwd=fwd_ret, rng=rng)


def stats_of(ret: np.ndarray):
    """ret 已含方向与成本。"""
    mask = ~np.isnan(ret)
    if mask.sum() < 20:
        return None
    r = ret[mask]
    return dict(n=int(mask.sum()), wr=float((r > 0).mean()),
                avg=float(r.mean()), net=float(r.sum()))


def run():
    all_rows = []          # (sym, combo_name, stats)
    split_rows = []        # (sym, combo_name, half, stats)
    feat_store = {}

    for sym in SYMBOLS:
        close, volume, gap_ok = load_klines(sym)
        f = build_features(close, volume)
        feat_store[sym] = f
        rp, rv, z20, zv, mom6, fwd = f["range_pos"], f["rsi"], f["z20"], f["zvol"], f["mom6"], f["fwd"]
        valid = (~np.isnan(rp)) & (~np.isnan(rv)) & (~np.isnan(fwd)) & (f["rng"] > 0) & gap_ok

        v_long = valid & (rp <= EDGE_LO) & (rv <= RSI_LO)
        v_short = valid & (rp >= EDGE_HI) & (rv >= RSI_HI)
        # 方向签名的收益
        ret_long = fwd - COST
        ret_short = -fwd - COST

        # 确认因子（多头方向：z20<-1, 放量, 动量下行；空头方向镜像）
        cf_z20_long = z20 < -1.0
        cf_z20_short = z20 > 1.0
        cf_vol_long = zv > 0.6
        cf_vol_short = zv > 0.6
        cf_mom_long = mom6 < -0.004
        cf_mom_short = mom6 > 0.004

        def build(name):
            """返回 (long_mask, short_mask)；None 表示跳过该组合（如边缘-only）。"""
            if name == "edge_only":
                return valid & (rp <= EDGE_LO), valid & (rp >= EDGE_HI)
            if name == "rsi_only":
                return v_long, v_short
            if name == "+z20":
                return v_long & cf_z20_long, v_short & cf_z20_short
            if name == "+vol":
                return v_long & cf_vol_long, v_short & cf_vol_short
            if name == "+mom":
                return v_long & cf_mom_long, v_short & cf_mom_short
            if name == "三因子AND":
                return (v_long & cf_z20_long & cf_vol_long & cf_mom_long,
                        v_short & cf_z20_short & cf_vol_short & cf_mom_short)
            if name == "三选二多数票":
                l = (v_long & ((cf_z20_long.astype(int) + cf_vol_long.astype(int) + cf_mom_long.astype(int)) >= 2))
                s = (v_short & ((cf_z20_short.astype(int) + cf_vol_short.astype(int) + cf_mom_short.astype(int)) >= 2))
                return l, s
            return None

        # 加权评分：score = w1*RSI_comp + w2*z20_comp + w3*vol_comp + w4*mom_comp
        #   RSI_comp(多) = (50-rsi)/10；空头镜像
        #   z20_comp(多) = -z20；vol_comp(多) = max(zvol,0)*0.8；mom_comp(多) = -mom6*100*1.5
        def weighted(dir_sign, th):
            if dir_sign == 1:
                score = (0.55 * ((50.0 - rv) / 10.0) + 0.20 * (-z20)
                         + 0.15 * np.maximum(zv, 0.0) * 0.8 + 0.10 * (-mom6 * 100.0) * 1.5)
                base = v_long
            else:
                score = (0.55 * ((rv - 50.0) / 10.0) + 0.20 * (z20)
                         + 0.15 * np.maximum(zv, 0.0) * 0.8 + 0.10 * (mom6 * 100.0) * 1.5)
                base = v_short
            return base & (score >= th)

        combos = ["edge_only", "rsi_only", "+z20", "+vol", "+mom", "三因子AND", "三选二多数票"]
        for name in combos:
            lm, sm = build(name)
            if lm is None or sm is None:
                continue
            ret = np.where(lm, ret_long, np.where(sm, ret_short, np.nan))
            st = stats_of(ret)
            if st:
                all_rows.append((sym, name, st))
                # 50/50 时间分割
                half = int(len(ret) * 0.5)
                for h, sl in (("前50%", slice(0, half)), ("后50%", slice(half, None))):
                    st_h = stats_of(ret[sl])
                    if st_h:
                        split_rows.append((sym, name, h, st_h))

        # 加权评分扫描
        for th in (0.30, 0.45, 0.60, 0.75, 0.90):
            lm, sm = weighted(1, th), weighted(-1, th)
            ret = np.where(lm, ret_long, np.where(sm, ret_short, np.nan))
            st = stats_of(ret)
            if st:
                all_rows.append((sym, f"加权T{th:.2f}", st))
                half = int(len(ret) * 0.5)
                for h, sl in (("前50%", slice(0, half)), ("后50%", slice(half, None))):
                    st_h = stats_of(ret[sl])
                    if st_h:
                        split_rows.append((sym, f"加权T{th:.2f}", h, st_h))

    df = pd.DataFrame(all_rows, columns=["sym", "combo", "stats"])
    agg = []
    for combo, sub in df.groupby("combo"):
        tot_n = int(sub["stats"].apply(lambda x: x["n"]).sum())
        awr = float(np.average(sub["stats"].apply(lambda x: x["wr"]), weights=sub["stats"].apply(lambda x: x["n"])))
        aret = float(np.average(sub["stats"].apply(lambda x: x["avg"]), weights=sub["stats"].apply(lambda x: x["n"])))
        agg.append((combo, tot_n, awr, aret))
    agg.sort(key=lambda x: -x[3])

    print(f"\n===== 多因子研究②（{PERIOD} {EXCHANGE} {len(SYMBOLS)}币, fwd={FWD}, cost={COST*1e4:.0f}bps, 方向已签名） =====")
    print(f"{'组合':<14}{'n':>6}   {'胜率':>7}   {'单笔净':>9}")
    for combo, tot_n, awr, aret in agg:
        flag = " ★" if aret > 0.0003 else ("  +" if aret > 0 else "  ·")
        print(f"  {combo:<14}{tot_n:>6}   {awr*100:>6.2f}%   {aret*100:>+8.3f}%{flag}")

    print("\n  各币 x 各组合 单笔净(%):")
    piv = df.pivot_table(index="sym", columns="combo", values="stats", aggfunc="first")
    for sym in SYMBOLS:
        parts = []
        for combo in [c[0] for c in agg]:
            row = piv.loc[sym]
            if combo in row.index and isinstance(row[combo], dict):
                parts.append(f"{combo}:{row[combo]['avg']*100:+.3f}")
        print(f"    {sym:<6} " + "  ".join(parts))

    print("\n  稳定性（前50% vs 后50% 单笔净%, 只显示净>0 组合）:")
    sdf = pd.DataFrame(split_rows, columns=["sym", "combo", "half", "stats"])
    for combo, sub in sdf.groupby("combo"):
        f50 = sub[sub["half"] == "前50%"]
        b50 = sub[sub["half"] == "后50%"]
        if f50.empty or b50.empty:
            continue
        ag = lambda d: float(np.average(d["stats"].apply(lambda x: x["avg"]), weights=d["stats"].apply(lambda x: x["n"]))) * 100
        a, b = ag(f50), ag(b50)
        both = "BOTH+" if a > 0 and b > 0 else ("both-" if a <= 0 and b <= 0 else "翻转")
        print(f"  {combo:<14} 前={a:+.3f}%  后={b:+.3f}%   [{both}]")


if __name__ == "__main__":
    run()
