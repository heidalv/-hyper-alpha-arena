#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
短线因子 · LightGBM 离线验证实验（PoC）
================================================================

目的（回答一个问题）：
    用现有 5m K线数据，**短线价量因子对"未来方向"到底有没有预测力**？
    以及——树模型（LightGBM）相比线性模型（逻辑回归）有没有优势？

严格纪律（避免自欺欺人）：
    1. 只看【样本外】成绩（walk-forward：用过去训练、用未来测试，绝不偷看未来）
    2. 同时训 LightGBM 和 逻辑回归，直接对比"树 vs 线性"
    3. 给出"瞎猜基线"（naive baseline），模型必须显著超过它才算有预测力
    4. 打印因子重要性排名（即使模型不上线，这也能帮筛因子）

不碰实盘、不改生产依赖，纯本地离线跑。

运行：
    backend\\.venv\\Scripts\\python.exe scripts\\poc_lgbm_scalp_factors.py
"""
from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
# Windows 控制台默认 GBK，强制 UTF-8 输出，避免打印 ✓/✗ 等符号时崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

# ── 实验参数 ──
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ASTER", "XPL"]
TIMEFRAME = "5m"
MAX_BARS = 100000            # 尽量多拉历史
HORIZON = 3                  # 预测未来几根 5m 的方向（3根=15分钟）
DEADZONE = 0.0015            # 死区：|未来收益|<0.15% 视为"横盘"，丢弃（降噪）
N_FOLDS = 4                  # walk-forward 折数（滚动样本外）
RANDOM_STATE = 42


# ============================================================
# 1. 特征工程：只用 OHLCV 就能算的向量化价量因子（全是整列运算，快）
#    绝大多数是"比率/标准化"型，天生可跨币池化（BTC/ETH 尺度不同也能合训）
# ============================================================
def _rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / (down + 1e-12)
    return 100 - 100 / (1 + rs)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    ret1 = np.log(c / c.shift(1))
    feat = pd.DataFrame(index=df.index)

    # 收益 / 动量
    for n in (1, 3, 6, 12, 24):
        feat[f"ret_{n}"] = np.log(c / c.shift(n))
    for n in (5, 10, 20):
        feat[f"mom_sma_{n}"] = c / c.rolling(n).mean() - 1.0
    feat["roc_10"] = c.pct_change(10)

    # 波动率
    for n in (6, 12, 24):
        feat[f"vol_std_{n}"] = ret1.rolling(n).std()
    feat["atr_rel_14"] = ((h - l) / c).rolling(14).mean()
    feat["hl_range"] = (h - l) / c

    # 均线结构
    ema5, ema10, ema20 = c.ewm(span=5).mean(), c.ewm(span=10).mean(), c.ewm(span=20).mean()
    feat["ema5_10"] = ema5 / ema10 - 1.0
    feat["ema10_20"] = ema10 / ema20 - 1.0
    # 布林位置
    sma20, std20 = c.rolling(20).mean(), c.rolling(20).std()
    feat["boll_pos"] = (c - sma20) / (2 * std20 + 1e-12)

    # RSI / MACD
    feat["rsi_6"] = _rsi(c, 6) / 100.0
    feat["rsi_14"] = _rsi(c, 14) / 100.0
    macd = c.ewm(span=12).mean() - c.ewm(span=26).mean()
    feat["macd_hist"] = (macd - macd.ewm(span=9).mean()) / c

    # 量能
    vsma = v.rolling(20).mean()
    feat["vol_ratio_20"] = v / (vsma + 1e-12)
    feat["vol_z_20"] = (v - vsma) / (v.rolling(20).std() + 1e-12)
    signed_vol = np.sign(c.diff()) * v
    feat["obv_slope_10"] = signed_vol.rolling(10).sum() / (vsma * 10 + 1e-12)

    # K线形态
    rng = (h - l) + 1e-12
    feat["body_ratio"] = (c - o) / rng
    feat["upper_wick"] = (h - np.maximum(o, c)) / rng
    feat["lower_wick"] = (np.minimum(o, c) - l) / rng

    # 距滚动高低点
    feat["dist_hi_20"] = c / h.rolling(20).max() - 1.0
    feat["dist_lo_20"] = c / l.rolling(20).min() - 1.0

    # 收益分布形态
    feat["ret_skew_20"] = ret1.rolling(20).skew()
    feat["ret_kurt_20"] = ret1.rolling(20).kurt()

    feat["ts"] = df["timestamp"].values
    return feat


def build_label(df: pd.DataFrame) -> pd.Series:
    c = df["close"]
    fwd = c.shift(-HORIZON) / c - 1.0
    lab = pd.Series(np.nan, index=df.index)
    lab[fwd > DEADZONE] = 1.0    # 未来上涨
    lab[fwd < -DEADZONE] = 0.0   # 未来下跌
    return lab                    # 中间横盘 = NaN，后面丢弃


# ============================================================
# 2. 组装多币数据集
# ============================================================
def load_dataset():
    from backend.services.kline_data_service import kline_service
    frames = []
    per_sym = {}
    for sym in SYMBOLS:
        rows = kline_service.get_klines_from_db(sym, TIMEFRAME, MAX_BARS, exchange="hyperliquid")
        if not rows or len(rows) < 200:
            print(f"  [skip] {sym}: 数据不足 ({len(rows) if rows else 0} 根)")
            continue
        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        feat = build_features(df)
        feat["y"] = build_label(df).values
        feat["symbol"] = sym
        feat = feat.dropna().reset_index(drop=True)
        per_sym[sym] = len(feat)
        frames.append(feat)
    if not frames:
        raise SystemExit("没有可用数据")
    data = pd.concat(frames, ignore_index=True)
    data = data.sort_values("ts").reset_index(drop=True)  # 按时间排，供 walk-forward
    return data, per_sym


# ============================================================
# 3. walk-forward 样本外评估
# ============================================================
def run():
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, accuracy_score
    import lightgbm as lgb

    print("=" * 64)
    print("短线因子 · LightGBM 离线验证（样本外 / walk-forward）")
    print("=" * 64)
    print(f"标的={SYMBOLS}  周期={TIMEFRAME}  预测未来={HORIZON}根  死区=±{DEADZONE:.2%}")

    data, per_sym = load_dataset()
    feat_cols = [c for c in data.columns if c not in ("y", "ts", "symbol")]
    print(f"\n【数据】特征数={len(feat_cols)}  可用样本(去横盘去NaN后)={len(data)}")
    for s, n in per_sym.items():
        print(f"    {s}: {n}")
    pos = data["y"].mean()
    print(f"    标签平衡: 涨={pos:.1%} 跌={1-pos:.1%}")

    X = data[feat_cols].values.astype(np.float64)
    y = data["y"].values.astype(int)
    ts = data["ts"].values

    # 按时间切成 N_FOLDS 段，逐段"用过去训练、测下一段"（扩展窗口）
    edges = np.quantile(ts, np.linspace(0, 1, N_FOLDS + 2))
    lgb_aucs, log_aucs, lgb_accs, naive_accs = [], [], [], []
    fi_accum = np.zeros(len(feat_cols))

    print("\n【walk-forward 样本外逐折结果】")
    print(f"{'fold':>4} {'train':>7} {'test':>6} {'LGB_AUC':>8} {'LOG_AUC':>8} {'LGB_acc':>8} {'瞎猜':>6}")
    for k in range(N_FOLDS):
        tr_hi = edges[k + 1]
        te_lo, te_hi = edges[k + 1], edges[k + 2]
        tr = ts < tr_hi
        te = (ts >= te_lo) & (ts < te_hi)
        if tr.sum() < 300 or te.sum() < 100:
            continue
        Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            continue

        # LightGBM（保守参数：小模型、强正则，抗过拟合）
        clf = lgb.LGBMClassifier(
            n_estimators=300, learning_rate=0.02, num_leaves=15,
            max_depth=4, min_child_samples=60, subsample=0.8,
            colsample_bytree=0.7, reg_lambda=5.0, reg_alpha=1.0,
            random_state=RANDOM_STATE, n_jobs=-1, verbose=-1,
        )
        clf.fit(Xtr, ytr)
        p_lgb = clf.predict_proba(Xte)[:, 1]
        fi_accum += clf.feature_importances_

        # 逻辑回归（线性 baseline，代表"现有线性加权"的能力上限）
        sc = StandardScaler().fit(Xtr)
        log = LogisticRegression(max_iter=1000, C=0.5)
        log.fit(sc.transform(Xtr), ytr)
        p_log = log.predict_proba(sc.transform(Xte))[:, 1]

        auc_lgb = roc_auc_score(yte, p_lgb)
        auc_log = roc_auc_score(yte, p_log)
        acc_lgb = accuracy_score(yte, (p_lgb > 0.5).astype(int))
        naive = max(yte.mean(), 1 - yte.mean())  # 全押多数类的准确率

        lgb_aucs.append(auc_lgb); log_aucs.append(auc_log)
        lgb_accs.append(acc_lgb); naive_accs.append(naive)
        print(f"{k:>4} {tr.sum():>7} {te.sum():>6} {auc_lgb:>8.3f} {auc_log:>8.3f} {acc_lgb:>8.3f} {naive:>6.3f}")

    if not lgb_aucs:
        print("\n[!] 有效折数不足，无法评估。")
        return

    print("\n【汇总（样本外均值）】")
    print(f"    LightGBM  AUC = {np.mean(lgb_aucs):.3f}   （0.5=瞎猜, >0.55 才算有点用, >0.6 不错）")
    print(f"    逻辑回归  AUC = {np.mean(log_aucs):.3f}   （线性 baseline）")
    print(f"    LightGBM  准确率 = {np.mean(lgb_accs):.3f}  vs  瞎猜基线 = {np.mean(naive_accs):.3f}")
    edge = np.mean(lgb_aucs) - 0.5
    tree_vs_lin = np.mean(lgb_aucs) - np.mean(log_aucs)
    print(f"    树模型相对线性优势 = {tree_vs_lin:+.3f} AUC")

    # 因子重要性
    fi = sorted(zip(feat_cols, fi_accum), key=lambda x: -x[1])
    print("\n【因子重要性 Top-20（LightGBM gain 累计）】")
    tot = sum(fi_accum) + 1e-12
    for name, imp in fi[:20]:
        bar = "█" * int(imp / fi[0][1] * 30) if fi[0][1] > 0 else ""
        print(f"    {name:>14}  {imp/tot:6.1%}  {bar}")

    # 诚实结论
    print("\n" + "=" * 64)
    print("【结论】")
    if np.mean(lgb_aucs) < 0.52:
        print("  ✗ 样本外 AUC ≈ 0.5，基本没预测力。当前数据/特征下，")
        print("    上 XGB/LightGBM 意义不大，建议先攒数据 / 加订单流特征。")
    elif np.mean(lgb_aucs) < 0.55:
        print("  △ 有一丝预测力但很弱。可作为辅助信号，别单独依赖；")
        print("    优先看下面的因子重要性，砍掉噪音因子后再试。")
    else:
        print("  ✓ 样本外有可观预测力，值得进一步做影子对比线性加权。")
    if tree_vs_lin > 0.01:
        print(f"  ✓ 树模型确实比线性强 {tree_vs_lin:+.3f}（抓到了非线性/交互）。")
    elif tree_vs_lin < -0.01:
        print(f"  ✗ 树模型不如线性（{tree_vs_lin:+.3f}），说明目前关系近似线性，")
        print("    没必要上树模型，现有线性加权已够。")
    else:
        print("  ≈ 树模型与线性打平，优先用更简单的线性。")
    print("=" * 64)


if __name__ == "__main__":
    run()
