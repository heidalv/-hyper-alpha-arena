#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断: 实盘被 hold 的信号,带/不带 K线特征 pwin 各是多少。"""
import sys, json
sys.path.insert(0, ".")
from backend.services.scalp_meta_trainer import predict_win_prob, compute_kline_feats
from backend.services.kline_data_service import kline_service

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

print("=== 实盘同源 klines_15m(kline_service,60根) ===")
for sym, d in (("VIRTUAL", "long"), ("XPL", "long"), ("ARC", "short"), ("SOL", "long")):
    rows = kline_service.get_klines_from_db(sym, "15m", 60)
    if not rows:
        print(f"{sym}: klines EMPTY")
        continue
    import pandas as pd
    df = pd.DataFrame(rows)
    print(f"{sym} klines cols={list(df.columns)} rows={len(df)}")
    kf = compute_kline_feats(df)
    print(f"  kf keys={sorted(kf.keys())} ema_slope={kf.get('ema_slope')} ret_1h={kf.get('ret_1h')}")
    feats = dict(snap_base)
    feats.update({"symbol": sym, "direction": d, "factor_score": 65.0 if d == "long" else 40.0})
    p_with = predict_win_prob(feats, require_usable=False, kline_feats=kf)
    p_without = predict_win_prob(feats, require_usable=False, kline_feats={})
    print(f"  {d:5s} pwin(带kline)={p_with} pwin(无kline)={p_without}")
