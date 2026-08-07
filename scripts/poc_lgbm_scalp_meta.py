#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
短线因子 · LightGBM 元标签（Meta-labeling）实验
================================================================

前两轮结论：用 ML 直接"猜方向"在本系统数据上走不通（样本外 AUC≈0.51）。
本轮换 López de Prado 的主流用法——【元标签】：
    不猜方向，而是给"现有 scalp 信号"当【真假过滤器】：
    "当规则发出开仓信号时，用 ML 预测这一单会不会赚。"

流程：
  1. 基础信号(side)：用动量+订单流合成一个方向分，过阈值(取高置信度那部分)才算"触发"
     —— 模拟 scalp_factor_router「分数过门槛才开仓」的行为。
  2. 标签(win/lose)：信号方向上、未来 N 根的净收益(扣手续费)是否>0。
  3. ML(LightGBM)：用 39 个因子预测"这单会不会赢"，walk-forward 只看样本外。
  4. 核心指标：被 ML 放行的信号，胜率 / 期望收益 比"照单全收"高多少（+覆盖率）。
     —— 元标签成功的标志不是 AUC 高，而是【过滤后胜率显著提升】。

复用 v2 的数据加载与因子（scripts/poc_lgbm_scalp_orderflow.py）。
不碰实盘、不改生产依赖。

运行：
    backend\\.venv\\Scripts\\python.exe scripts\\poc_lgbm_scalp_meta.py
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
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))
sys.path.insert(0, _HERE)

import numpy as np
import pandas as pd

import poc_lgbm_scalp_orderflow as v2  # 复用 load_symbol_5m / build_features / 因子列

# ── 参数 ──
META_HORIZON = 6          # 信号后看未来 6 根 5m（30分钟）判定输赢
ROUND_TRIP_COST = 0.0008  # 单边≈0.04%，往返≈0.08%（taker）
CONVICTION_Q = 0.60       # 基础信号"触发"门槛：合成分绝对值处于前 40%
N_FOLDS = 5
RANDOM_STATE = 42
EPS = 1e-12


def _z(s: pd.Series) -> pd.Series:
    return (s - s.rolling(50).mean()) / (s.rolling(50).std() + EPS)


def build_base_signal_and_meta(bar: pd.DataFrame, feat: pd.DataFrame):
    """在特征表上构造：基础信号方向 side、是否触发 active、元标签 y_meta。"""
    c = bar["close"].reindex(feat.index)
    # 合成方向分（动量 + 订单流确认），模拟路由器的多因子合成
    comp = _z(feat["ret_3"]) + _z(feat["ema10_20"]) + _z(feat["mom_sma_10"])
    if "taker_imb_ma6" in feat:
        comp = comp + 0.5 * _z(feat["taker_imb_ma6"])
    if "cvd_cum_z_20" in feat:
        comp = comp + 0.5 * feat["cvd_cum_z_20"].clip(-3, 3)
    side = np.sign(comp)
    conv = comp.abs()
    thr = conv.quantile(CONVICTION_Q)
    active = (conv >= thr) & (side != 0)

    # 未来净收益（信号方向上，扣往返成本）
    fwd = c.shift(-META_HORIZON) / c - 1.0
    net = side * fwd - ROUND_TRIP_COST
    y_meta = (net > 0).astype(float)

    out = feat.copy()
    out["side"] = side.values
    out["active"] = active.values
    out["y_meta"] = y_meta.values
    out["net_ret"] = net.values
    out["gross_dir_ret"] = (side * fwd).values
    return out


def run():
    from sklearn.metrics import roc_auc_score
    import lightgbm as lgb
    from backend.database.connection import MarketSessionLocal

    print("=" * 66)
    print("短线因子 · LightGBM 元标签实验（信号真假过滤器）")
    print("=" * 66)
    print(f"信号后 {META_HORIZON} 根(={META_HORIZON*5}分钟)判输赢  往返成本={ROUND_TRIP_COST:.2%}"
          f"  触发门槛=前{(1-CONVICTION_Q)*100:.0f}%置信度")

    s = MarketSessionLocal()
    frames = []
    try:
        for sym in v2.SYMBOLS:
            try:
                bar = v2.load_symbol_5m(s, sym)
            except Exception:
                continue
            if bar.empty or len(bar) < 400:
                continue
            feat = v2.build_features(bar)  # 设置 v2.PRICE_COLS / v2.FLOW_COLS
            feat = feat.drop(columns=["y"], errors="ignore")
            merged = build_base_signal_and_meta(bar, feat)
            merged = merged.replace([np.inf, -np.inf], np.nan)
            merged["symbol"] = sym
            frames.append(merged)
    finally:
        s.close()
    if not frames:
        print("无数据"); return

    feat_cols = v2.PRICE_COLS + v2.FLOW_COLS
    data = pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)
    data = data.dropna(subset=feat_cols + ["y_meta", "side", "net_ret"])
    sig = data[data["active"]].reset_index(drop=True)  # 只在"信号触发"样本上做元标签
    print(f"\n【数据】特征={len(feat_cols)}  总bar={len(data)}  触发信号数={len(sig)}")
    base_wr = sig["y_meta"].mean()
    base_ev = sig["net_ret"].mean()
    print(f"    照单全收基线：胜率={base_wr:.1%}  平均净收益={base_ev:+.4%}/单")

    X = sig[feat_cols].fillna(0.0).values.astype(np.float64)
    y = sig["y_meta"].values.astype(int)
    ts = sig["ts"].values
    net = sig["net_ret"].values
    edges = np.quantile(ts, np.linspace(0, 1, N_FOLDS + 2))

    aucs = []
    # 收集样本外预测，评估"过滤后"表现
    oos_p, oos_y, oos_net = [], [], []
    fi = np.zeros(len(feat_cols))
    for k in range(N_FOLDS):
        tr = ts < edges[k + 1]
        te = (ts >= edges[k + 1]) & (ts < edges[k + 2])
        if tr.sum() < 400 or te.sum() < 150:
            continue
        if len(np.unique(y[tr])) < 2:
            continue
        clf = lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.02, num_leaves=16, max_depth=4,
            min_child_samples=80, subsample=0.8, colsample_bytree=0.7,
            reg_lambda=5.0, reg_alpha=1.0, random_state=RANDOM_STATE,
            n_jobs=-1, verbose=-1)
        clf.fit(X[tr], y[tr])
        p = clf.predict_proba(X[te])[:, 1]
        fi += clf.feature_importances_
        if len(np.unique(y[te])) > 1:
            aucs.append(roc_auc_score(y[te], p))
        oos_p.append(p); oos_y.append(y[te]); oos_net.append(net[te])

    if not oos_p:
        print("有效折不足"); return
    p = np.concatenate(oos_p); yy = np.concatenate(oos_y); nn = np.concatenate(oos_net)

    print(f"\n【元模型样本外 AUC】= {np.mean(aucs):.3f}")

    print("\n【过滤效果：ML 放行 vs 照单全收】")
    print(f"    {'策略':<22}{'覆盖率':>8}{'胜率':>8}{'平均净收益/单':>14}")
    print(f"    {'照单全收(基线)':<22}{100.0:>7.0f}%{base_wr:>7.1%}{base_ev:>13.4%}")
    for label, mask in [
        ("ML prob>0.50", p > 0.50),
        ("ML prob>0.55", p > 0.55),
        ("ML 取概率前50%", p >= np.quantile(p, 0.50)),
        ("ML 取概率前30%", p >= np.quantile(p, 0.70)),
        ("ML 取概率前15%", p >= np.quantile(p, 0.85)),
    ]:
        if mask.sum() < 30:
            print(f"    {label:<22}{'样本太少':>8}")
            continue
        cov = mask.mean()
        wr = yy[mask].mean()
        ev = nn[mask].mean()
        print(f"    {label:<22}{cov:>7.0%}{wr:>8.1%}{ev:>13.4%}")

    # 因子重要性
    fis = sorted(zip(feat_cols, fi), key=lambda x: -x[1])
    print("\n【元模型 · 因子重要性 Top-15】(★=订单流)")
    tot = sum(v for _, v in fis) + EPS
    top = fis[0][1] or 1
    for name, imp in fis[:15]:
        star = "★" if name in v2.FLOW_COLS else " "
        print(f"    {star}{name:>16}  {imp/tot:6.1%}  " + "█" * int(imp / top * 26))

    # 结论
    best_wr = yy[p >= np.quantile(p, 0.85)].mean() if (p >= np.quantile(p, 0.85)).sum() >= 30 else wr
    best_ev = nn[p >= np.quantile(p, 0.85)].mean() if (p >= np.quantile(p, 0.85)).sum() >= 30 else ev
    print("\n" + "=" * 66)
    print("【结论】")
    wr_lift = best_wr - base_wr
    ev_lift = best_ev - base_ev
    print(f"  基线胜率 {base_wr:.1%} / 净收益 {base_ev:+.4%}  →  "
          f"ML严格过滤(前15%) 胜率 {best_wr:.1%} / 净收益 {best_ev:+.4%}")
    print(f"  胜率提升 {wr_lift:+.1%}，净收益提升 {ev_lift:+.4%}/单")
    if ev_lift > 0.0003 and best_ev > 0:
        print("  ✓ 元标签有效：ML 过滤后信号明显更赚，值得推进（影子对比现有闸门）。")
    elif wr_lift > 0.02:
        print("  △ 胜率有提升但净收益边际，可作为 EV 闸门的一个附加特征试试。")
    else:
        print("  ✗ 元标签在当前数据/基础信号下没带来实质提升。")
    print("=" * 66)


if __name__ == "__main__":
    run()
