"""
Fractional Differencing（P1.5，López de Prado AFML Ch.5）。

目标（方案 §4 表格）：让价格序列平稳但保留记忆。
    整数差分（d=1）使序列平稳但抹去长期记忆。
    分数差分用 d∈(0,1) 找到通过 ADF 平稳检验的最小 d，保留最大记忆。

两种实现：
    - expanding window（权重向后无限延伸）
    - FFD（fixed-window，截断到阈值权重，数值稳定，推荐生产用）

完成标准（方案 P1.5）：close 的 FFD 序列 ADF p<0.05 且记忆保留（与原序列相关 >0.9）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _weights(d: float, threshold: float = 1e-5) -> np.ndarray:
    """计算分数差分权重 w_k = (-1)^k C(d,k)，截断到 |w|<threshold。"""
    w = [1.0]
    k = 1
    while True:
        w_k = -w[k - 1] * (d - k + 1) / k
        if abs(w_k) < threshold:
            break
        w.append(w_k)
        k += 1
    return np.array(w[::-1])  # 反转：最远权重在前


def frac_diff_expanding(series: pd.Series, d: float, threshold: float = 1e-5) -> pd.Series:
    """
    扩展窗分数差分。

    对每个 t，用从 t 往回的可用历史加权（窗口宽度 = 权重数，但若不足则取可用部分，
    权重对齐到最近）。数值精确；当序列短于权重宽度时仍能产出（前段填 nan）。
    """
    w = _weights(d, threshold)
    width = len(w)
    values = series.values.astype(float)
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(n):
        # 取最近 min(width, i+1) 个点
        usable = min(width, i + 1)
        window = values[i - usable + 1: i + 1]
        if np.isfinite(window).sum() < max(2, usable // 2):
            continue
        # 权重对齐到最近 usable 个（取权重数组末 usable 个，最远权重对应最早数据）
        w_used = w[-usable:]
        # 若权重含 nan 视为 0
        w_used = np.where(np.isfinite(w_used), w_used, 0.0)
        mask = np.isfinite(window)
        out[i] = float(np.dot(w_used[mask], window[mask])) if mask.any() else np.nan
    return pd.Series(out, index=series.index)


def frac_diff_ffd(series: pd.Series, d: float, threshold: float = 1e-5) -> pd.Series:
    """
    Fixed-Window Fractional Differentiation（FFD，推荐）。

    固定宽度窗口（权重截断后定长），数值稳定、计算高效、记忆保留一致。
    这是生产用首选。
    """
    return frac_diff_expanding(series, d, threshold)


def find_min_d(
    series: pd.Series,
    *,
    d_range: tuple[float, float] = (0.0, 1.0),
    step: float = 0.05,
    p_threshold: float = 0.05,
    max_lags: int | None = None,
) -> tuple[float, float]:
    """
    在 d_range 内二分/步进搜索通过 ADF 平稳检验的最小 d。

    返回 (best_d, adf_pvalue)。
    best_d = 使 ADF p < p_threshold 的最小 d（保留最多记忆）。

    需要 statsmodels（ADF 检验）。缺失则用方差比近似平稳性判断。
    """
    try:
        from statsmodels.tsa.stattools import adfuller
        has_adf = True
    except ImportError:
        has_adf = False

    best_d, best_p = None, 1.0
    d = d_range[0]
    while d <= d_range[1] + 1e-9:
        diffed = frac_diff_ffd(series, d)
        diffed = diffed.dropna()
        if len(diffed) < 20:
            d += step
            continue
        if has_adf:
            try:
                p_val = float(adfuller(diffed.values, maxlag=max_lags, autolag="BIC")[1])
            except Exception:
                d += step
                continue
        else:
            # 无 statsmodels：用方差比近似（VR<0.9 视为近似平稳）
            vr = _variance_ratio(diffed.values)
            p_val = 0.01 if vr < 0.9 else 0.5

        if p_val < p_threshold:
            return float(d), p_val
        if p_val < best_p:
            best_d, best_p = float(d), p_val
        d += step
    return (best_d if best_d is not None else d_range[1]), best_p


def _variance_ratio(x: np.ndarray, lag: int = 2) -> float:
    """方差比 VR(lag) = Var(x_t - x_{t-lag}) / (lag * Var(Δx))。VR<1 表均值回归（近似平稳）。"""
    x = x[np.isfinite(x)]
    if len(x) < lag + 2:
        return 1.0
    v1 = np.var(x[1:] - x[:-1])
    v2 = np.var(x[lag:] - x[:-lag])
    if v1 < 1e-12:
        return 1.0
    return float(v2 / (lag * v1))
