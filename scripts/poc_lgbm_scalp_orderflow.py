#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
短线因子 · LightGBM 离线验证 v2（含订单流因子）
================================================================

相比 v1（只用纯价量因子，样本外 AUC≈0.5 无预测力），v2 补上短线真正的 alpha 来源：
    订单流 / 衍生品因子（CVD、OI 变化、资金费率、premium、盘口深度失衡、价差）。

数据来源（都是 30 天、亚分钟级历史，重采样为 5m）：
    - 价格/OI/资金费率/premium ← market_asset_metrics（有 mark_price，30天）
    - CVD/taker 买卖失衡        ← market_trades_aggregated（15s 聚合成交）
    - 盘口深度/价差            ← market_orderbook_snapshots

核心问题（v2 要回答的）：
    加了订单流因子后，样本外预测力比"纯价量"提升了吗？
    → 脚本会跑两套特征（price-only vs price+orderflow）直接对比 AUC。

严格纪律同 v1：只看样本外(walk-forward)、树 vs 线性对比、瞎猜基线、因子重要性。
不碰实盘、不改生产依赖。

运行：
    backend\\.venv\\Scripts\\python.exe scripts\\poc_lgbm_scalp_orderflow.py
"""
from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

# ── 参数 ──
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ASTER", "XPL", "HYPE", "AVAX",
           "NEAR", "ONDO", "WLD", "UNI", "INJ", "TAO", "ENA", "XLM"]
EXCHANGE = "hyperliquid"
BAR = "5min"
HORIZON = 3                # 预测未来 3 根 5m（15分钟）
DEADZONE = 0.0015          # ±0.15% 死区，去横盘
N_FOLDS = 5
RANDOM_STATE = 42
EPS = 1e-12


# ============================================================
# 1. 从三张表拉数据，重采样为 5m
# ============================================================
def _q_df(session, model, sym, cols):
    from sqlalchemy import asc
    rows = (session.query(*[getattr(model, c) for c in cols])
            .filter(model.symbol == sym, model.exchange == EXCHANGE)
            .order_by(asc(model.timestamp)).all())
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=cols)
    for c in cols:
        if c != "timestamp":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("dt")


def load_symbol_5m(session, sym) -> pd.DataFrame:
    from backend.database.models import (
        MarketAssetMetrics, MarketTradesAggregated, MarketOrderbookSnapshots)

    m = _q_df(session, MarketAssetMetrics, sym,
              ["timestamp", "mark_price", "mid_price", "open_interest",
               "funding_rate", "premium"])
    if m.empty or m["mark_price"].notna().sum() < 500:
        return pd.DataFrame()
    price = m["mark_price"].fillna(m["mid_price"])
    bar = pd.DataFrame({
        "open": price.resample(BAR).first(),
        "high": price.resample(BAR).max(),
        "low": price.resample(BAR).min(),
        "close": price.resample(BAR).last(),
        "oi": m["open_interest"].resample(BAR).last(),
        "funding": m["funding_rate"].resample(BAR).last(),
        "premium": m["premium"].resample(BAR).last(),
    })

    t = _q_df(session, MarketTradesAggregated, sym,
              ["timestamp", "taker_buy_notional", "taker_sell_notional",
               "taker_buy_volume", "taker_sell_volume",
               "taker_buy_count", "taker_sell_count"])
    if not t.empty:
        bn = t["taker_buy_notional"].resample(BAR).sum()
        sn = t["taker_sell_notional"].resample(BAR).sum()
        bv = t["taker_buy_volume"].resample(BAR).sum()
        sv = t["taker_sell_volume"].resample(BAR).sum()
        bc = t["taker_buy_count"].resample(BAR).sum()
        sc = t["taker_sell_count"].resample(BAR).sum()
        bar["volume"] = (bv + sv)
        bar["cvd_bar"] = (bn - sn)
        bar["taker_imb"] = (bn - sn) / (bn + sn + EPS)
        bar["count_imb"] = (bc - sc) / (bc + sc + EPS)
    else:
        bar["volume"] = np.nan

    ob = _q_df(session, MarketOrderbookSnapshots, sym,
               ["timestamp", "best_bid", "best_ask", "spread",
                "bid_depth_5", "ask_depth_5"])
    if not ob.empty:
        mid = (ob["best_bid"] + ob["best_ask"]) / 2
        bar["spread_rel"] = (ob["spread"] / (mid + EPS)).resample(BAR).mean()
        bd = ob["bid_depth_5"].resample(BAR).mean()
        ad = ob["ask_depth_5"].resample(BAR).mean()
        bar["depth_imb"] = (bd - ad) / (bd + ad + EPS)

    bar = bar.dropna(subset=["close"]).copy()
    bar["ts"] = (bar.index.view("int64") // 10**9)
    return bar


# ============================================================
# 2. 特征工程
# ============================================================
def _rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / (dn + EPS))


PRICE_COLS = []
FLOW_COLS = []


def build_features(bar: pd.DataFrame) -> pd.DataFrame:
    global PRICE_COLS, FLOW_COLS
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    v = bar["volume"].fillna(0)
    ret1 = np.log(c / c.shift(1))
    f = pd.DataFrame(index=bar.index)

    # ---- 价量因子（与 v1 同族）----
    for n in (1, 3, 6, 12, 24):
        f[f"ret_{n}"] = np.log(c / c.shift(n))
    for n in (5, 10, 20):
        f[f"mom_sma_{n}"] = c / c.rolling(n).mean() - 1.0
    for n in (6, 12, 24):
        f[f"vol_std_{n}"] = ret1.rolling(n).std()
    f["atr_rel_14"] = ((h - l) / c).rolling(14).mean()
    ema5, ema10, ema20 = c.ewm(span=5).mean(), c.ewm(span=10).mean(), c.ewm(span=20).mean()
    f["ema5_10"] = ema5 / ema10 - 1.0
    f["ema10_20"] = ema10 / ema20 - 1.0
    sma20, std20 = c.rolling(20).mean(), c.rolling(20).std()
    f["boll_pos"] = (c - sma20) / (2 * std20 + EPS)
    f["rsi_6"] = _rsi(c, 6) / 100.0
    f["rsi_14"] = _rsi(c, 14) / 100.0
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    f["macd_hist"] = (macd - macd.ewm(span=9).mean()) / c
    vsma = v.rolling(20).mean()
    f["vol_ratio_20"] = v / (vsma + EPS)
    f["dist_hi_20"] = c / h.rolling(20).max() - 1.0
    f["dist_lo_20"] = c / l.rolling(20).min() - 1.0
    f["ret_skew_20"] = ret1.rolling(20).skew()
    f["ret_kurt_20"] = ret1.rolling(20).kurt()
    PRICE_COLS = list(f.columns)

    # ---- 订单流 / 衍生品因子 ----
    if "cvd_bar" in bar:
        cvd = bar["cvd_bar"].fillna(0)
        notional = v.abs() + EPS
        f["cvd_norm"] = cvd / (notional * c + EPS)
        f["cvd_cum_z_20"] = (cvd.rolling(20).sum() /
                             (cvd.rolling(20).std() * np.sqrt(20) + EPS))
        f["taker_imb"] = bar["taker_imb"].fillna(0)
        f["taker_imb_ma6"] = bar["taker_imb"].rolling(6).mean()
        f["count_imb"] = bar["count_imb"].fillna(0)
        # CVD 与价格背离：价涨但 CVD 流出（或反之）
        f["cvd_price_div"] = np.sign(ret1) * -np.sign(cvd)
    if "oi" in bar:
        oi = bar["oi"]
        for n in (1, 3, 6):
            f[f"oi_delta_{n}"] = oi.pct_change(n)
        # OI 增 + 价涨 = 多头建仓；OI 增 + 价跌 = 空头建仓（方向信息）
        f["oi_price_align"] = np.sign(oi.pct_change(3)) * np.sign(f["ret_3"])
    if "funding" in bar:
        fund = bar["funding"].fillna(method="ffill")
        f["funding"] = fund
        f["funding_z_50"] = (fund - fund.rolling(50).mean()) / (fund.rolling(50).std() + EPS)
    if "premium" in bar:
        f["premium"] = bar["premium"].fillna(0)
    if "depth_imb" in bar:
        f["depth_imb"] = bar["depth_imb"].fillna(0)
        f["depth_imb_ma6"] = bar["depth_imb"].rolling(6).mean()
    if "spread_rel" in bar:
        f["spread_rel"] = bar["spread_rel"].fillna(method="ffill")
    FLOW_COLS = [c2 for c2 in f.columns if c2 not in PRICE_COLS]

    # 标签
    fwd = c.shift(-HORIZON) / c - 1.0
    y = pd.Series(np.nan, index=bar.index)
    y[fwd > DEADZONE] = 1.0
    y[fwd < -DEADZONE] = 0.0
    f["y"] = y
    f["ts"] = bar["ts"].values
    return f


# ============================================================
# 3. walk-forward 评估（对某个特征列集合）
# ============================================================
def evaluate(data, feat_cols, title):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, accuracy_score
    import lightgbm as lgb

    X = data[feat_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0).values.astype(np.float64)
    y = data["y"].values.astype(int)
    ts = data["ts"].values
    edges = np.quantile(ts, np.linspace(0, 1, N_FOLDS + 2))
    lgb_aucs, log_aucs, lgb_accs, naive_accs = [], [], [], []
    fi = np.zeros(len(feat_cols))
    for k in range(N_FOLDS):
        tr = ts < edges[k + 1]
        te = (ts >= edges[k + 1]) & (ts < edges[k + 2])
        if tr.sum() < 500 or te.sum() < 200:
            continue
        Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            continue
        clf = lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.02, num_leaves=16, max_depth=4,
            min_child_samples=80, subsample=0.8, colsample_bytree=0.7,
            reg_lambda=5.0, reg_alpha=1.0, random_state=RANDOM_STATE,
            n_jobs=-1, verbose=-1)
        clf.fit(Xtr, ytr)
        p = clf.predict_proba(Xte)[:, 1]
        fi += clf.feature_importances_
        sc = StandardScaler().fit(Xtr)
        log = LogisticRegression(max_iter=1000, C=0.5).fit(sc.transform(Xtr), ytr)
        pl = log.predict_proba(sc.transform(Xte))[:, 1]
        lgb_aucs.append(roc_auc_score(yte, p))
        log_aucs.append(roc_auc_score(yte, pl))
        lgb_accs.append(accuracy_score(yte, (p > 0.5).astype(int)))
        naive_accs.append(max(yte.mean(), 1 - yte.mean()))
    res = {
        "title": title, "n_feat": len(feat_cols),
        "lgb_auc": float(np.mean(lgb_aucs)) if lgb_aucs else float("nan"),
        "log_auc": float(np.mean(log_aucs)) if log_aucs else float("nan"),
        "lgb_acc": float(np.mean(lgb_accs)) if lgb_accs else float("nan"),
        "naive": float(np.mean(naive_accs)) if naive_accs else float("nan"),
        "fi": sorted(zip(feat_cols, fi), key=lambda x: -x[1]),
    }
    return res


def run():
    from backend.database.connection import MarketSessionLocal
    print("=" * 64)
    print("短线因子 · LightGBM v2（含订单流）· 样本外对比实验")
    print("=" * 64)
    s = MarketSessionLocal()
    frames, per_sym = [], {}
    try:
        for sym in SYMBOLS:
            try:
                bar = load_symbol_5m(s, sym)
            except Exception as e:
                print(f"  [skip] {sym}: {e!r}"); continue
            if bar.empty or len(bar) < 300:
                print(f"  [skip] {sym}: bars={len(bar)}"); continue
            f = build_features(bar).dropna(subset=["y"])
            f = f.dropna()
            if len(f) < 200:
                print(f"  [skip] {sym}: usable={len(f)}"); continue
            f["symbol"] = sym
            frames.append(f)
            per_sym[sym] = len(f)
    finally:
        s.close()
    if not frames:
        print("无可用数据"); return
    data = pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)

    print(f"\n【数据】价量因子={len(PRICE_COLS)}  订单流因子={len(FLOW_COLS)}  "
          f"总样本(去横盘)={len(data)}")
    print("    每币样本:", {k: v for k, v in per_sym.items()})
    print(f"    标签平衡: 涨={data['y'].mean():.1%}")

    r_price = evaluate(data, PRICE_COLS, "纯价量")
    r_all = evaluate(data, PRICE_COLS + FLOW_COLS, "价量+订单流")

    print("\n【样本外对比（walk-forward 均值 AUC）】")
    print(f"    {'特征集':<14}{'特征数':>6}{'LGB_AUC':>10}{'线性_AUC':>10}{'LGB准确率':>10}{'瞎猜':>8}")
    for r in (r_price, r_all):
        print(f"    {r['title']:<14}{r['n_feat']:>6}{r['lgb_auc']:>10.3f}"
              f"{r['log_auc']:>10.3f}{r['lgb_acc']:>10.3f}{r['naive']:>8.3f}")
    lift = r_all["lgb_auc"] - r_price["lgb_auc"]
    print(f"\n    订单流带来的 AUC 增益 = {lift:+.3f}")

    print("\n【价量+订单流 · 因子重要性 Top-20】(★=订单流因子)")
    tot = sum(v for _, v in r_all["fi"]) + EPS
    top = r_all["fi"][0][1] or 1
    for name, imp in r_all["fi"][:20]:
        star = "★" if name in FLOW_COLS else " "
        bar = "█" * int(imp / top * 28)
        print(f"    {star}{name:>16}  {imp/tot:6.1%}  {bar}")

    print("\n" + "=" * 64)
    print("【结论】")
    a = r_all["lgb_auc"]
    if a >= 0.55:
        print(f"  ✓ 加订单流后样本外 AUC={a:.3f}，有可观预测力，值得进影子对比。")
    elif a >= 0.52:
        print(f"  △ AUC={a:.3f}，弱预测力，可做辅助信号，先按重要性精简因子。")
    else:
        print(f"  ✗ AUC={a:.3f}，仍接近瞎猜，短线方向依旧难学。")
    if lift >= 0.015:
        print(f"  ✓ 订单流确实加分（+{lift:.3f}）——短线 alpha 更多在订单流而非纯K线。")
    elif lift <= -0.005:
        print(f"  ✗ 订单流没帮上忙（{lift:+.3f}）。")
    else:
        print(f"  ≈ 订单流增益有限（{lift:+.3f}）。")
    n_flow_in_top = sum(1 for name, _ in r_all["fi"][:10] if name in FLOW_COLS)
    print(f"  · Top-10 重要因子里订单流占 {n_flow_in_top} 个。")
    print("=" * 64)


if __name__ == "__main__":
    run()
