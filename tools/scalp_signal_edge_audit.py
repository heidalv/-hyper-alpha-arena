#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scalp signal edge audit v2 (offline, read-only).

v2: adds lagged rolling regime proxies to test REAL-TIME usability
(no lookahead: features only use rows whose outcome would already be
settled at signal time — approximated by row-lags of 5/15/30/60).
"""
import json, os, sys, time, math
import numpy as np
import pandas as pd

DB_URL = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"

def load_rows(days=45):
    import psycopg
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT created_at, symbol, direction, factor_score, win, fwd_ret, net_ret, features_json "
                "FROM scalp_signal_log WHERE settled = true AND win IS NOT NULL "
                "AND created_at >= NOW() - make_interval(days => %s) ORDER BY created_at", (days,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

NUM_KEYS = ("composite", "raw_dir", "crypto_alpha", "rsi", "rsi_extreme",
            "range_position", "pos_extreme", "amplitude_pct", "of_buy_notional",
            "of_sell_notional", "of_cvd", "of_depth_ratio", "of_imbalance",
            "of_funding_rate", "of_oi_delta_pct", "cycle_prob_up", "cycle_prob_down",
            "cycle_prob_agreement", "cycle_prob_confidence", "cycle_prob_calibration",
            "trend_boost")

def build_df(rows):
    recs = []
    for r in rows:
        try:
            feats = json.loads(r["features_json"]) if r["features_json"] else {}
        except Exception:
            feats = {}
        rec = {
            "ts": r["created_at"], "symbol": r["symbol"],
            "direction": r["direction"], "factor_score": float(r["factor_score"] or 0),
            "win": int(r["win"]), "fwd_ret": float(r["fwd_ret"] or 0),
            "net_ret": float(r["net_ret"] or 0),
            "dir_sign": 1.0 if r["direction"] == "long" else -1.0,
        }
        for k in NUM_KEYS:
            v = feats.get(k)
            if isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                rec[k] = float(v)
        recs.append(rec)
    df = pd.DataFrame(recs)
    df["ts"] = pd.to_datetime(df["ts"])
    df["hour"] = df["ts"].dt.hour.astype(float)
    df["weekday"] = df["ts"].dt.weekday.astype(float)
    df = df.sort_values("ts").reset_index(drop=True)
    return df

def add_rolling(df):
    """Per-symbol rolling regime proxies with multiple row-lags (no lookahead)."""
    out = pd.DataFrame(index=df.index)
    g = df.groupby("symbol")
    for lag in (1, 5, 15, 30, 60):
        out[f"wr20_lag{lag}"] = g["win"].transform(lambda s: s.shift(lag).rolling(20, min_periods=5).mean())
        out[f"fwd20_lag{lag}"] = g["fwd_ret"].transform(lambda s: s.shift(lag).rolling(20, min_periods=5).mean())
    if "rsi" in df:
        out["roll_rsi10"] = g["rsi"].transform(lambda s: s.shift(1).rolling(10, min_periods=5).mean())
    else:
        out["roll_rsi10"] = np.nan
    if "of_cvd" in df:
        out["roll_cvd10"] = g["of_cvd"].transform(lambda s: s.shift(1).rolling(10, min_periods=5).mean())
    else:
        out["roll_cvd10"] = np.nan
    out["sec_since_sig"] = g["ts"].transform(lambda s: s.diff().dt.total_seconds())
    cvd = df["of_cvd"].values if "of_cvd" in df else np.full(len(df), np.nan)
    out["cvd_x_dir"] = np.where(~np.isnan(cvd), cvd * df["dir_sign"].values, np.nan)
    return out

BASELINE_COLS = ["factor_score", "dir_sign", "composite", "raw_dir", "crypto_alpha",
                 "rsi", "rsi_extreme", "range_position", "pos_extreme", "amplitude_pct",
                 "of_buy_notional", "of_sell_notional", "of_cvd", "of_depth_ratio",
                 "of_imbalance", "of_funding_rate", "of_oi_delta_pct", "cycle_prob_up",
                 "cycle_prob_down", "cycle_prob_agreement", "cycle_prob_confidence",
                 "cycle_prob_calibration", "trend_boost"]

def fillna_cols(df, cols):
    X = df[cols].copy()
    for c in cols:
        X[c] = pd.to_numeric(X[c], errors="coerce")
        X[c] = X[c].fillna(0.0)
    return X.values.astype(np.float64)

def rule_checks(df):
    out = {}
    d = df
    def s(label, mask):
        m = mask.fillna(False).astype(bool)
        n = int(m.sum())
        if n < 100:
            out[label] = f"n={n} (too few)"
            return
        out[label] = f"n={n} wr={d.loc[m,'win'].mean()*100:.1f}% net={d.loc[m,'net_ret'].mean()*100:.4f}%"
    s("all", pd.Series(True, index=d.index))
    for lag in (1, 5, 15, 30, 60):
        s(f"fwd20_lag{lag}>0", d[f"fwd20_lag{lag}"] > 0)
        s(f"fwd20_lag{lag}<0", d[f"fwd20_lag{lag}"] < 0)
        s(f"wr20_lag{lag}>=0.6", d[f"wr20_lag{lag}"] >= 0.6)
        s(f"wr20_lag{lag}<0.35", d[f"wr20_lag{lag}"] < 0.35)
    s("long&fwd20_lag30>0", (d["direction"] == "long") & (d["fwd20_lag30"] > 0))
    s("long&fwd20_lag30<0", (d["direction"] == "long") & (d["fwd20_lag30"] < 0))
    s("short&fwd20_lag30<0", (d["direction"] == "short") & (d["fwd20_lag30"] < 0))
    s("short&fwd20_lag30>0", (d["direction"] == "short") & (d["fwd20_lag30"] > 0))
    s("cvd_with_dir", d["cvd_x_dir"] > 0)
    s("cvd_against_dir", d["cvd_x_dir"] < 0)
    s("hour6-10", (d["hour"] >= 6) & (d["hour"] < 10))
    s("fwd20_lag30>0&hour6-10", (d["fwd20_lag30"] > 0) & (d["hour"] >= 6) & (d["hour"] < 10))
    return out

def wf_auc(X, y, ts, folds=5, seed=42):
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    edges = np.quantile(ts, np.linspace(0, 1, folds + 2))
    aucs, ps = [], []
    for k in range(folds):
        tr = ts < edges[k + 1]
        te = (ts >= edges[k + 1]) & (ts < edges[k + 2])
        if tr.sum() < 500 or te.sum() < 100 or len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        clf = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=15,
                                 max_depth=4, min_child_samples=100, subsample=0.8,
                                 colsample_bytree=0.7, reg_lambda=5.0, random_state=seed,
                                 n_jobs=4, verbose=-1).fit(X[tr], y[tr])
        p = clf.predict_proba(X[te])[:, 1]
        aucs.append(roc_auc_score(y[te], p))
        ps.append((np.flatnonzero(te), p))
    if not ps:
        return None, None
    te_all = np.concatenate([t for t, _ in ps])
    p_all = np.concatenate([p for _, p in ps])
    return float(np.mean(aucs)), (te_all, p_all)

def main():
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] loading rows ...", flush=True)
    rows = load_rows(days=int(os.getenv("AUDIT_DAYS", "45")))
    print(f"[{time.strftime('%H:%M:%S')}] loaded {len(rows)} settled rows", flush=True)
    df = build_df(rows)
    print(f"[{time.strftime('%H:%M:%S')}] built df {df.shape}; adding rolling ...", flush=True)
    roll = add_rolling(df)
    df = pd.concat([df, roll], axis=1)
    print(f"[{time.strftime('%H:%M:%S')}] rolling done ({time.time()-t0:.0f}s)", flush=True)
    print("")
    print("=== RULE CHECKS (no model) ===", flush=True)
    for k, v in rule_checks(df).items():
        print(f"  {k:26s} {v}", flush=True)
    y = df["win"].values.astype(int)
    net = df["net_ret"].values
    ts = df["ts"].astype("int64").values // 10**9
    print("")
    print("=== WALK-FORWARD MODELS ===", flush=True)
    reg_cols = ["wr20_lag1", "wr20_lag5", "wr20_lag15", "wr20_lag30", "wr20_lag60",
                "fwd20_lag1", "fwd20_lag5", "fwd20_lag15", "fwd20_lag30", "fwd20_lag60",
                "roll_rsi10", "roll_cvd10", "sec_since_sig", "cvd_x_dir", "hour", "weekday"]
    for name, cols in [("baseline", BASELINE_COLS),
                       ("baseline+regime", BASELINE_COLS + reg_cols)]:
        print(f"  training {name} ...", flush=True)
        X = fillna_cols(df, [c for c in cols if c in df.columns])
        auc, pack = wf_auc(X, y, ts)
        if auc is None:
            print(f"  {name:22s} no valid folds", flush=True); continue
        te, p = pack
        for q, lab in [(0.70, "top30%"), (0.85, "top15%")]:
            thr = np.quantile(p, q)
            m = p >= thr
            print(f"  {name:22s} AUC={auc:.4f} {lab}: n={int(m.sum())} wr={y[te][m].mean()*100:.1f}% net={net[te][m].mean()*100:.4f}%", flush=True)
        print(f"    base wr={y.mean()*100:.1f}% net={net.mean()*100:.4f}%", flush=True)
    print("")
    print(f"done in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
