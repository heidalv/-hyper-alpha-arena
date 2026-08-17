# -*- coding: utf-8 -*-
"""方向对齐实验：rsi@4h 三种 orientation 策略的 OOS 表现对比。"""
import os, sys, warnings, time
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.services.factor_engine.midlong_registry_factors import _rolling_recompute
from backend.services.factor_engine.factor_calculator import FactorCalculator
from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

FWD = 6
COST = 0.0021
FUNDING = 0.0001 * (FWD * 4 / 8.0)

def wf(factor_vals, closes, fwd, orient_mode):
    n = len(closes)
    fwd_ret = np.full(n, np.nan)
    fwd_ret[:-fwd] = (closes[fwd:] - closes[:-fwd]) / closes[:-fwd]
    f = factor_vals.copy()
    mask = np.isfinite(f) & np.isfinite(fwd_ret)
    idx = np.where(mask)[0]
    if len(idx) < 60:
        return []
    folds = 3
    seg = len(idx) // folds
    oos_returns = []
    for k in range(1, folds):
        train_idx = idx[(k-1)*seg: k*seg]
        test_idx = idx[k*seg: (k+1)*seg] if k < folds-1 else idx[k*seg:]
        if len(train_idx) < 20 or len(test_idx) < 10:
            continue
        tf = f[train_idx]; tr = fwd_ret[train_idx]
        if np.std(tf) < 1e-12 or np.std(tr) < 1e-12:
            continue
        mu, sd = np.mean(tf), np.std(tf)
        if sd < 1e-12:
            continue
        sample = test_idx[::max(1, fwd)]
        prev_pos = 0.0
        for t in sample:
            z = (f[t] - mu) / sd
            if orient_mode == "fold":
                ic = float(np.corrcoef(tf, tr)[0, 1])
                orient = 1.0 if ic >= 0 else -1.0
            elif orient_mode == "none":
                orient = 1.0
            elif orient_mode == "trail":
                lo = max(0, t - 120)
                hi = max(lo + 60, t - fwd)  # 只用 t 前已实现的前向收益
                tt = np.arange(lo, hi)
                tfw = f[tt]; trw = fwd_ret[tt]
                m2 = np.isfinite(tfw) & np.isfinite(trw)
                if m2.sum() < 30 or np.std(tfw[m2]) < 1e-12 or np.std(trw[m2]) < 1e-12:
                    orient = 1.0
                else:
                    ic = float(np.corrcoef(tfw[m2], trw[m2])[0, 1])
                    orient = 1.0 if ic >= 0 else -1.0
            else:
                raise ValueError(orient_mode)
            pos = np.sign(z) * orient
            r = fwd_ret[t]
            if not np.isfinite(r):
                continue
            gross = pos * r
            turn = abs(pos - prev_pos)
            oos_returns.append(float(gross - COST * (turn / 2.0) - FUNDING))
            prev_pos = pos
    return oos_returns

calc = FactorCalculator()
for sym in ("BTC", "ETH", "SOL"):
    klines = factor_backtest_scorer._load_klines(sym, "4h", 900)
    df = pd.DataFrame(klines)
    vals = _rolling_recompute(calc, "rsi", df, sym, "4h", FWD)
    closes = df["close"].astype(float).to_numpy()
    full_ic = float(np.corrcoef(vals[~np.isnan(vals)], 
        (pd.Series(closes).shift(-FWD)/pd.Series(closes) - 1).iloc[~np.isnan(vals)].to_numpy())[0,1]) if False else None
    row = {}
    for mode in ("fold", "none", "trail"):
        rs = np.array(wf(vals, closes, FWD, mode))
        if len(rs):
            ann = 365*24/4
            sharpe = rs.mean()/(rs.std()+1e-12)*np.sqrt(min(ann/FWD, len(rs)))
            row[mode] = (round(rs.sum(), 4), round(sharpe, 3), round(float((rs>0).mean()), 3), len(rs))
    print(sym, row, flush=True)
