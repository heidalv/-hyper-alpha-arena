# -*- coding: utf-8 -*-
"""挖矿信号诊断(2026-08-27): 用挖掘管线同一数据源+同一评估器测试经典因子族。

目的: 判定挖矿挖出垃圾是 (a) 标签/数据管道坏了(经典因子也全负) 还是
(b) GP 特征空间/搜索太弱(经典因子有信号, GP 找不到)。
"""
import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.kline_data_service import kline_service
from backend.services.factor_engine.evaluation import evaluate_factor
from backend.services.evolution.factor_evolution_loop import _forward_returns, _fwd_bars_for_period

SYMS = ["BTC", "ETH", "SOL", "BNB", "ASTER", "UNI", "VIRTUAL", "XPL", "XRP"]
PERIOD = "4h"
LIMIT = 720

def load(sym):
    rows = kline_service.get_klines_from_db(sym, PERIOD, LIMIT)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for c in ("open","high","low","close","volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close","high","low","volume"]).reset_index(drop=True)
    return df

def build_factors(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    ret = c.pct_change()
    out = {}
    out["mom_1"] = ret
    out["mom_5"] = c.pct_change(5)
    out["mom_10"] = c.pct_change(10)
    out["mom_20"] = c.pct_change(20)
    out["rev_5"] = -c.pct_change(5)
    out["vol_20"] = ret.rolling(20).std()
    out["vol_ratio"] = ret.rolling(5).std() / (ret.rolling(40).std() + 1e-12)
    out["vol_delta"] = c.pct_change().abs().rolling(5).mean() - c.pct_change().abs().rolling(20).mean()
    out["rsi14"] = (c.diff().clip(lower=0).rolling(14).mean() / (c.diff().abs().rolling(14).mean() + 1e-12) - 0.5) * 2
    out["macd"] = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    out["close_ma20"] = c / (c.rolling(20).mean() + 1e-12) - 1
    out["bb_pos"] = (c - c.rolling(20).mean()) / (c.rolling(20).std() + 1e-12)
    out["hl_range"] = (h - l) / c
    out["vol_z"] = (v - v.rolling(40).mean()) / (v.rolling(40).std() + 1e-12)
    out["amihud"] = (c.pct_change().abs() / (v * c + 1e-12)).rolling(20).mean()
    out["intrabar_ret"] = (c - l) / (h - l + 1e-12) - 0.5
    return out

def main():
    fwd_bars = _fwd_bars_for_period()
    print("period=%s fwd_bars=%s" % (PERIOD, fwd_bars))
    agg = {}
    for sym in SYMS:
        df = load(sym)
        if df is None or len(df) < 200:
            print(sym, "no data")
            continue
        fwd = pd.Series(_forward_returns(df), index=df.index)
        facs = build_factors(df)
        for name, fv in facs.items():
            fv = pd.Series(fv, index=df.index)
            mask = np.isfinite(fv) & np.isfinite(fwd)
            if mask.sum() < 50:
                continue
            r = evaluate_factor("diag_" + name, fv[mask], fwd[mask])
            d = agg.setdefault(name, {"icir": [], "ic": [], "n": 0})
            d["icir"].append(r.icir)
            d["ic"].append(r.ic_mean)
            d["n"] += int(mask.sum())
    print()
    print("%-14s %8s %8s %8s" % ("factor", "avg_ic", "avg_icir", "pos_syms"))
    for name in sorted(agg):
        d = agg[name]
        ic = float(np.mean(d["ic"]))
        icir = float(np.mean(d["icir"]))
        pos = sum(1 for x in d["icir"] if x > 0)
        print("%-14s %+8.4f %+8.3f %6d/%d" % (name, ic, icir, pos, len(d["icir"])))

if __name__ == "__main__":
    main()
