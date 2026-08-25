#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 sanity check: 用最新 K线 + 典型信号特征跑 v3 模型,看 pwin 分布。"""
import sys, json
sys.path.insert(0, ".")
import psycopg
import pandas as pd

from backend.services.scalp_meta_trainer import predict_win_prob, compute_kline_feats, get_report

rep = get_report()
print("report:", {k: rep.get(k) for k in ("status", "usable", "oos_auc_lgbm", "label", "features")})

def latest_klines(symbol, n=60):
    with psycopg.connect("postgresql://laobao:alpha_pass@localhost:5432/alpha_market") as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp, open_price, high_price, low_price, close_price, volume "
                "FROM crypto_klines WHERE period='15m' AND symbol=%s "
                "ORDER BY timestamp DESC LIMIT %s", (symbol, n))
            rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"]).iloc[::-1].reset_index(drop=True)

snap_base = {
    "factor_score": 65.0,
    "composite": 65.0, "raw_dir": 0.6, "crypto_alpha": 0,
    "rsi": 30.0, "rsi_extreme": 0.2, "range_position": 0.15,
    "pos_extreme": 0.1, "amplitude_pct": 0.02,
    "of_buy_notional": 1e6, "of_sell_notional": 8e5, "of_cvd": -2e5,
    "of_depth_ratio": 1.2, "of_imbalance": -0.2, "of_funding_rate": 0.0001,
    "of_oi_delta_pct": 0.01,
    "cycle_prob_up": 0.5, "cycle_prob_down": 0.5, "cycle_prob_agreement": 0.0,
    "cycle_prob_confidence": 0.5, "cycle_prob_calibration": 0.05, "cycle_prob_delta": 0,
    "trend_boost": 0.0,
}

for sym in ("BTC", "ETH", "SOL", "UNI", "ASTER"):
    kf = compute_kline_feats(latest_klines(sym))
    for d in ("long", "short"):
        feats = dict(snap_base)
        feats.update({"symbol": sym, "direction": d, "factor_score": 65.0 if d == "long" else 40.0})
        p = predict_win_prob(feats, require_usable=False, kline_feats=kf)
        if p is None:
            print(f"{sym:7s} {d:5s} pwin=None (模型缺失?)")
            continue
        trend = "up" if kf.get("ema_slope", 0) > 0 else ("down" if kf.get("ema_slope", 0) < 0 else "flat")
        print(f"{sym:7s} {d:5s} pwin={p:.3f} | kline: ema_slope={kf.get('ema_slope',0):+.4f} ret_1h={kf.get('ret_1h',0):+.4f} ret_4h={kf.get('ret_4h',0):+.4f} rsi15m={kf.get('rsi15m',0):.0f} ({trend})")
