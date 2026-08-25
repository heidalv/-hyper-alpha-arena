#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit v4: honest kline trend features (fully vectorized per symbol).

Joins settled scalp signals with 15m klines at signal time (strictly bars
BEFORE ts). Rules + LGBM walk-forward.
"""
import json, os, sys, time, math
import numpy as np
import pandas as pd

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"

def load_signals(days=45):
    import psycopg
    with psycopg.connect(ARENA) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT signal_ts, created_at, symbol, direction, factor_score, win, fwd_ret, net_ret, features_json "
                "FROM scalp_signal_log WHERE settled=true AND win IS NOT NULL "
                "AND created_at >= NOW() - make_interval(days => %s) ORDER BY created_at", (days,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

def load_klines(symbols):
    import psycopg
    out = {}
    with psycopg.connect(MARKET) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, timestamp, open_price, high_price, low_price, close_price, volume "
                "FROM crypto_klines WHERE period='15m' AND symbol = ANY(%s) ORDER BY symbol, timestamp",
                (list(symbols),))
            for sym, ts, o, h, l, c, v in cur.fetchall():
                out.setdefault(sym, []).append((int(ts), float(o), float(h), float(l), float(c), float(v or 0)))
    for sym in out:
        out[sym] = np.array(out[sym], dtype=np.float64)
    return out

def rsi_series(closes, n=14):
    s = pd.Series(closes)
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return (100 - 100/(1+rs)).values

def build_features(signals, klines):
    df = pd.DataFrame(signals)
    df["ts"] = df["signal_ts"].fillna(0).astype(np.int64)
    m = df["ts"] <= 0
    df.loc[m, "ts"] = pd.to_datetime(df.loc[m, "created_at"]).astype("int64") // 10**9
    feat_cols = ["ret_15m", "ret_1h", "ret_4h", "ema_slope", "rsi15m",
                 "atr_pct", "vol_1h", "below_ema20"]
    for k in feat_cols:
        df[k] = np.nan
    for sym, grp in df.groupby("symbol"):
        arr = klines.get(sym)
        if arr is None or len(arr) < 40:
            continue
        kts = arr[:, 0]; closes = arr[:, 4]; highs = arr[:, 2]; lows = arr[:, 3]
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().values
        rsi = rsi_series(closes)
        idx = np.searchsorted(kts, grp["ts"].values, side="right") - 1
        valid = idx >= 39
        idx = idx[valid]
        gi = grp.index.values[valid]
        if len(idx) == 0:
            continue
        c = closes[idx]
        # ATR(14) 序列(逐币一次,按 close 归一)
        tr = np.maximum(highs[1:] - lows[1:],
                        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        atr_series = pd.Series(tr).rolling(14).mean().values
        atr_series = np.concatenate([[np.nan], atr_series])
        vals = {
            "ret_15m": c / closes[idx-1] - 1,
            "ret_1h": c / closes[idx-4] - 1,
            "ret_4h": c / closes[idx-16] - 1,
            "ema_slope": (c - ema20[idx]) / ema20[idx],
            "rsi15m": rsi[idx],
            "atr_pct": atr_series[idx] / c,
            "below_ema20": (c < ema20[idx]).astype(float),
        }
        w = np.empty(len(idx))
        for j, ii in enumerate(idx):
            seg = closes[ii-4:ii+1]
            w[j] = np.std(np.diff(seg)) / closes[ii]
        vals["vol_1h"] = w
        for k, v in vals.items():
            df.loc[gi, k] = v
    df["hour"] = pd.to_datetime(df["created_at"]).dt.hour.astype(float)
    df["weekday"] = pd.to_datetime(df["created_at"]).dt.weekday.astype(float)
    return df

def rules(df):
    d = df.dropna(subset=["ret_1h"])
    print("=== RULES (kline trend features) ===", flush=True)
    def s(label, mask):
        m = mask.astype(bool)
        n = int(m.sum())
        if n < 100:
            print(f"  {label:26s} n={n} (too few)", flush=True); return
        print(f"  {label:26s} n={n} wr={d.loc[m,'win'].mean()*100:.1f}% net={d.loc[m,'net_ret'].mean()*100:.4f}%", flush=True)
    s("all", pd.Series(True, index=d.index))
    s("long&ret_1h<0", (d.direction=="long") & (d.ret_1h<0))
    s("long&ret_1h>0", (d.direction=="long") & (d.ret_1h>0))
    s("long&ret_4h<0", (d.direction=="long") & (d.ret_4h<0))
    s("long&ret_4h>0", (d.direction=="long") & (d.ret_4h>0))
    s("long&ema_slope<0", (d.direction=="long") & (d.ema_slope<0))
    s("long&ema_slope>0", (d.direction=="long") & (d.ema_slope>0))
    s("short&ret_1h>0", (d.direction=="short") & (d.ret_1h>0))
    s("short&ret_1h<0", (d.direction=="short") & (d.ret_1h<0))
    s("short&ema_slope>0", (d.direction=="short") & (d.ema_slope>0))
    s("short&ema_slope<0", (d.direction=="short") & (d.ema_slope<0))
    s("ret_4h<-0.02", d.ret_4h < -0.02)
    s("ret_4h>0.02", d.ret_4h > 0.02)

def wf(df, cols, folds=5):
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    X = df[cols].fillna(0.0).values.astype(np.float64)
    y = (df["win"].values > 0).astype(int)
    ts = df["ts"].values
    edges = np.quantile(ts, np.linspace(0, 1, folds+2))
    aucs, ps = [], []
    for k in range(folds):
        tr = ts < edges[k+1]
        te = (ts >= edges[k+1]) & (ts < edges[k+2])
        if tr.sum() < 500 or te.sum() < 100 or y[tr].min() == y[tr].max() or y[te].min() == y[te].max():
            continue
        clf = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=15,
                                 max_depth=4, min_child_samples=100, subsample=0.8,
                                 colsample_bytree=0.7, reg_lambda=5.0, random_state=42,
                                 n_jobs=4, verbose=-1).fit(X[tr], y[tr])
        p = clf.predict_proba(X[te])[:, 1]
        aucs.append(roc_auc_score(y[te], p))
        ps.append((np.flatnonzero(te), p))
    if not ps: return None
    te = np.concatenate([t for t,_ in ps]); p = np.concatenate([p for _,p in ps])
    thr = np.quantile(p, 0.7); m = p >= thr
    net = df["net_ret"].values
    return float(np.mean(aucs)), int(m.sum()), float(y[te][m].mean()), float(net[te][m].mean())

def main():
    t0 = time.time()
    sigs = load_signals()
    syms = {s["symbol"] for s in sigs}
    print(f"{len(sigs)} signals, {len(syms)} symbols", flush=True)
    kl = load_klines(syms)
    print(f"klines loaded for {len(kl)} symbols in {time.time()-t0:.0f}s", flush=True)
    df = build_features(sigs, kl)
    print(f"features built in {time.time()-t0:.0f}s, rows={len(df)}, with_kline={int(df['ret_1h'].notna().sum())}", flush=True)
    rules(df)
    df["dir_long"] = (df.direction == "long").astype(float)
    df["dir_short"] = (df.direction == "short").astype(float)
    base_cols = ["factor_score", "dir_long", "dir_short"]
    kline_cols = ["ret_15m", "ret_1h", "ret_4h", "ema_slope", "rsi15m", "atr_pct", "vol_1h", "below_ema20", "hour", "weekday"]
    print("\n=== WALK-FORWARD ===", flush=True)
    for name, cols in [("base(score+dir)", base_cols),
                       ("base+kline", base_cols + kline_cols)]:
        r = wf(df, cols)
        if r is None: print(f"  {name}: no folds", flush=True); continue
        auc, n, wr, net = r
        print(f"  {name:16s} AUC={auc:.4f} top30%: n={n} wr={wr*100:.1f}% net={net*100:.4f}%", flush=True)
    print(f"done in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
