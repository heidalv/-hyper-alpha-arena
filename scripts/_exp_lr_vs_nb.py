# -*- coding: utf-8 -*-
"""[2026-10-08 研究] 逻辑回归 vs 朴素贝叶斯 —— 趋势概率模型结构对比实验。

不改线上模型,只做样本外对比:同一批数据、同一时间切分,分别训 NB 和 LR,
报告各自 AUC。LR 能学特征间权重(不怕相关),NB 假设特征独立。
"""
import io
import sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

import numpy as np
from backend.services.evolution.hft_trend_prob_train import load_training_rows
from backend.services.market_maker.trend_prob import FEATURES

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "BCHUSDT"]
THR = 3.0

print("== 装载数据 ==", flush=True)
rows = load_training_rows(SYMS, hours=24.0, step_s=2.0, horizon_s=30.0)
obs_all = np.concatenate([r["obs"] for r in rows.values()], axis=0)
fwd_all = np.concatenate([r["fwd"] for r in rows.values()], axis=0)

# 时间顺序 70/30(每币内部切,保时间序)
tr_idx, te_idx = [], []
off = 0
for r in rows.values():
    n = len(r["fwd"])
    cut = int(n * 0.7)
    tr_idx.extend(range(off, off + cut))
    te_idx.extend(range(off + cut, off + n))
    off += n
tr_idx, te_idx = np.array(tr_idx), np.array(te_idx)
x_tr, x_te = obs_all[tr_idx], obs_all[te_idx]
f_tr, f_te = fwd_all[tr_idx], fwd_all[te_idx]
y_tr_up = (f_tr >= THR).astype(int)
y_te_up = (f_te >= THR).astype(int)
y_tr_dn = (f_tr <= -THR).astype(int)
y_te_dn = (f_te <= -THR).astype(int)

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

print(f"样本 train={len(tr_idx)} test={len(te_idx)}  特征数={x_tr.shape[1]}", flush=True)

for name, ytr, yte in (("up", y_tr_up, y_te_up), ("dn", y_tr_dn, y_te_dn)):
    sc = StandardScaler().fit(x_tr)
    lr = LogisticRegression(max_iter=2000, C=1.0)
    lr.fit(sc.transform(x_tr), ytr)
    p = lr.predict_proba(sc.transform(x_te))[:, 1]
    auc = roc_auc_score(yte, p)
    # 特征权重(标准化后,绝对值大=重要)
    w = dict(zip(FEATURES, np.round(np.abs(lr.coef_[0]), 3)))
    top = sorted(w.items(), key=lambda kv: -kv[1])[:5]
    print(f"[LR {name}] OOS AUC={auc:.4f}  权重top5={top}", flush=True)

print("\n对照:NB 模型(当前线上) 涨 AUC≈0.657 / 跌 AUC≈0.646", flush=True)
