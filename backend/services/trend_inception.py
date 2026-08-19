"""trend_inception — 趋势起始点检测（设计总方案 B1，2026-08-19）。

BOCPD（Adams & MacKay 2007，高斯均值变点，Student-t 观测）对价格序列递推
「变点后验概率」；趋势起始点 = 变点概率显著 + 多信号确认：
  腿1 L1 翻多（trend_layer state 翻转）
  腿2 BOCPD 变点概率（最近一根 > 阈值）
  腿3 微观博弈确认（B5 microstructure_gauge 接线后启用，当前 None）
confirmed = 腿1 且 腿2（腿3 接线前）；腿3 就绪后改为三选二。
纯 numpy 递推，CPU 毫秒级，无前视（递推只用截至当前的数据）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

_HZ_DEFAULT = 1.0 / 250.0   # 先验变点频率：一年约 1 次
_CHANGE_THRESHOLD = 0.3     # 变点概率确认阈值


def bocpd_change_prob(x: np.ndarray, hazard: float = _HZ_DEFAULT,
                      var: Optional[float] = None) -> np.ndarray:
    """BOCPD 递推：返回每步 P(run_length=0 | x_1:t)（变点后验概率）。

    观测模型：x_t ~ N(mu, sigma^2)，mu 共轭高斯先验 N(0, kappa*sigma^2)。
    O(n^2) 标量递推（n=1200 约 0.5s），每日调用一次且上层有缓存。
    """
    n = len(x)
    if n < 10:
        return np.zeros(n)
    if var is None:
        var = float(np.var(x[-120:]) if len(x) >= 120 else np.var(x)) or 1e-6
    kappa = 1.0
    sigma = float(np.sqrt(var))
    change = np.zeros(n)
    p = np.ones(1)  # 初始 run length 0 概率 1
    means = np.array([0.0])   # run length r 的后验均值
    counts = np.array([kappa])  # 后验精度计数（kappa + r）
    for t in range(1, n):
        xt = float(x[t])
        # 预测：run length r 的预测分布 N(mean_r, sigma^2 * (1 + 1/count_r))
        pred_sd = sigma * np.sqrt(1.0 + 1.0 / np.maximum(counts, 1e-12))
        z = (xt - means) / np.maximum(pred_sd, 1e-12)
        logpred = -0.5 * z * z - np.log(pred_sd) - 0.5 * np.log(2 * np.pi)
        pred = np.exp(logpred - np.max(logpred))
        # 新 run（变点）：预测用先验 N(0, sigma^2*(1+1/kappa))
        z0 = xt / (sigma * np.sqrt(1.0 + 1.0 / kappa))
        pred0 = float(np.exp(-0.5 * z0 * z0) / (sigma * np.sqrt(1.0 + 1.0 / kappa) * np.sqrt(2 * np.pi)))
        un = np.zeros(t + 1)
        un[0] = hazard * float(np.sum(p)) * pred0
        un[1:] = (1.0 - hazard) * p * pred
        total = float(un.sum())
        if total <= 0 or not np.isfinite(total):
            break
        p = un / total
        change[t] = float(p[0])
        # 更新后验（run length r 的均值/精度）
        new_means = np.empty(t + 1)
        new_counts = np.empty(t + 1)
        new_means[0] = 0.0
        new_counts[0] = kappa
        new_means[1:] = (counts * means + xt) / (counts + 1.0)
        new_counts[1:] = counts + 1.0
        means = new_means
        counts = new_counts
    return change


def inception_check(df, l1_states=None, leg3: Optional[bool] = None) -> Dict[str, Any]:
    """趋势起始点检测：返回各腿状态与确认结果。

    df: 已收盘 1d K 线（含 open/high/low/close）
    l1_states: 可选，classify_series 的 state 序列（省一次重算）
    """
    out: Dict[str, Any] = {
        "change_prob": 0.0, "leg1_l1_flip": False, "leg2_bocpd": False,
        "leg3_micro": None, "confirmed": False, "score": 0.0,
    }
    try:
        import pandas as pd
        from backend.services.trend_layer import classify_series

        closes = df["close"].astype(float).values
        if len(closes) < 300:
            return out
        cp = bocpd_change_prob(closes)
        out["change_prob"] = round(float(cp[-1]), 4)
        out["leg2_bocpd"] = bool(cp[-1] > _CHANGE_THRESHOLD)
        # 腿1：L1 翻多（最近一根 up 且上一根非 up）
        if l1_states is None:
            cls = classify_series(df)
            l1_states = cls["state"].values
        if len(l1_states) >= 2:
            out["leg1_l1_flip"] = bool(l1_states[-1] == "up" and l1_states[-2] != "up")
        # [B5] 腿3 微观博弈确认（microstructure_gauge.gauge.confirmed 传入）
        out["leg3_micro"] = leg3
        if leg3 is None:
            # 腿3 未接线：确认 = 腿1 且 腿2
            out["confirmed"] = bool(out["leg1_l1_flip"] and out["leg2_bocpd"])
            _legs = [1.0 if out["leg1_l1_flip"] else 0.0,
                     1.0 if out["leg2_bocpd"] else 0.0]
            out["score"] = round(float(np.mean(_legs)), 2)
        else:
            # 三选二确认
            _legs = [1.0 if out["leg1_l1_flip"] else 0.0,
                     1.0 if out["leg2_bocpd"] else 0.0,
                     1.0 if leg3 else 0.0]
            out["confirmed"] = bool(sum(_legs) >= 2)
            out["score"] = round(float(sum(_legs) / 3.0), 2)
    except Exception as e:
        logger.debug("[TrendInception] 检测失败: %s", e)
    return out
