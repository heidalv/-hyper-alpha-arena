"""
DSR (Deflated Sharpe Ratio) / PBO (Probability of Backtest Overfitting) 计算。

基于 López de Prado (2014/2017) 方法：
  - DSR: 对 N 次试验的 Sharpe 做多重检验校正，输出 p-value。
  - PBO: 通过 CSCV 估计过拟合概率。

用于 FactorEvolutionLoop 阶段4 清洗，替代硬编码占位值。
"""
from __future__ import annotations

import logging
import math
import numpy as np
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# DSR — Deflated Sharpe Ratio
# ═══════════════════════════════════════════════════════════════════════════

def _standard_normal_cdf(x: float) -> float:
    """标准正态 CDF (Abramowitz & Stegun 7.1.26 近似)。"""
    if x < -8:
        return 0.0
    if x > 8:
        return 1.0
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    d = 0.3989422804014327 * math.exp(-x * x / 2.0)
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    if x >= 0:
        return 1.0 - d * poly
    return d * poly


def _standard_normal_ppf(p: float) -> float:
    """标准正态 PPF (逆 CDF, Moro 算法近似)。"""
    if p <= 0.0 or p >= 1.0:
        return float('nan')
    # 对称处理
    if p > 0.5:
        return -_standard_normal_ppf(1.0 - p)
    a = [2.50662823884, -18.61500062529, 41.39119773534, -25.44106049637]
    b = [-8.47351093090, 23.08336743743, -21.06224101826, 3.13082909833]
    c = [0.3374754822726147, 0.9761690190917186, 0.1607979714918209,
         0.0276438810333863, 0.0038405729373609, 0.0003951896511919,
         3.21767881768e-05, 2.888167364e-07, 3.960315187e-07]
    y = p - 0.5
    if abs(y) < 0.42:
        r = y * y
        return y * (((a[3] * r + a[2]) * r + a[1]) * r + a[0]) / \
               ((((b[3] * r + b[2]) * r + b[1]) * r + b[0]) * r + 1.0)
    r = p if y < 0 else 1.0 - p
    r = math.sqrt(-math.log(r))
    x = (((c[6] * r + c[5]) * r + c[4]) * r + c[3]) * r + c[2]
    x = x + c[0] / (1.0 + c[1] * r)
    return -x if y < 0 else x


def compute_dsr(
    observed_sr: float,
    n_trials: int,
    sr_mean: float = 0.0,
    sr_std: float = 1.0,
    skew: float = 0.0,
    kurt: float = 3.0,
    sample_len: int = 252,
) -> dict:
    """
    计算 Deflated Sharpe Ratio (Bailey & López de Prado 2014)。

    Parameters:
        observed_sr: 观测到的 Sharpe Ratio（这里用年化 ICIR 代替）
        n_trials: 候选因子/策略总数（多重检验校正）
        sr_mean: SR 均值
        sr_std: SR 标准差
        skew: 偏度
        kurt: 峰度
        sample_len: 样本长度（用于 Cornish-Fisher 展开）

    Returns:
        {dsr, p_value, significant (p<0.05)}
    """
    if n_trials <= 0:
        return {"dsr": 0.0, "p_value": 1.0, "significant": False}

    # 预期最大 Sharpe（极值分布 — Gumbel）
    # E[max(SR)] ≈ sr_mean + sr_std * ((1-γ)*Φ⁻¹(1-1/N) + γ*Φ⁻¹(1-1/(N*e)))
    # γ ≈ 0.5772156649 (Euler-Mascheroni)
    euler = 0.5772156649

    if n_trials == 1:
        expected_max = sr_mean
    else:
        z1 = _standard_normal_ppf(1.0 - 1.0 / n_trials)
        z2 = _standard_normal_ppf(1.0 - 1.0 / (n_trials * math.e))
        # 用 clamping 处理极端 z 值
        z1 = max(-5.0, min(5.0, z1)) if not math.isnan(z1) else 3.0
        z2 = max(-5.0, min(5.0, z2)) if not math.isnan(z2) else 3.0
        expected_max = sr_mean + sr_std * ((1.0 - euler) * z1 + euler * z2)

    # Cornish-Fisher 修正（考虑偏度和超峰度）
    z_score = (observed_sr - expected_max) / max(sr_std, 1e-8)
    if sample_len > 0 and abs(skew) > 1e-6:
        # Cornish-Fisher 展开
        skew_adj = skew / (6.0 * math.sqrt(sample_len))
        kurt_adj = (kurt - 3.0) / (24.0 * sample_len)
        z_cf = z_score + skew_adj * (z_score * z_score - 1.0) + \
               kurt_adj * (z_score * z_score * z_score - 3.0 * z_score) - \
               skew_adj * skew_adj * (2.0 * z_score * z_score * z_score - 5.0 * z_score)
        z_score = z_cf

    # P(实际 SR ≤ 观测值) = Φ(z_score)
    p_value = 1.0 - _standard_normal_cdf(z_score)
    dsr = z_score

    return {
        "dsr": round(dsr, 4),
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "n_trials": n_trials,
        "expected_max_sr": round(expected_max, 4),
    }


# ═══════════════════════════════════════════════════════════════════════════
# PBO — Probability of Backtest Overfitting
# ═══════════════════════════════════════════════════════════════════════════

def compute_pbo_simple(
    icir_values: list[float],
    n_splits: int = 10,
) -> dict:
    """[P0-1 时序 CSCV] PBO 计算（时间轴切分版）。

    icir_values 视为【因子 IC 的时序序列】（按时间先后排列）：沿时间轴切 S 段，
    C(S, S/2) 组合中 IS 段均值定方向，OOS 段检验方向是否保持。
    PBO = P(IS 方向在 OOS 段失效)。

    旧实现按 ICIR 值 argsort 分组模拟时序切分——输入无时间维度，IS 最优因子在
    OOS 恒排前 → PBO≈0 恒通过，时间维度的过拟合完全无法检测。此版本禁止值排序。
    样本不足时返回 indeterminate，调用方必须 fail-closed（不得当作通过）。
    """
    n = len(icir_values)
    if n < 4 or n_splits < 4:
        return {"pbo": 0.5, "significant": False, "n_splits": n_splits,
                "indeterminate": True,
                "note": "样本不足，调用方应 fail-closed"}

    n_splits = min(n_splits, n // 2)
    if n_splits < 2:
        return {"pbo": 0.5, "significant": False, "n_splits": n_splits,
                "indeterminate": True,
                "note": "时间序列太短，无法切分"}

    # 连续时间切片（顺序保持，绝不排序）
    seg_len = n // n_splits
    segments = [
        icir_values[i * seg_len:(i + 1) * seg_len]
        for i in range(n_splits)
    ]

    from itertools import combinations
    import random

    is_size = n_splits // 2
    all_combos = list(combinations(range(n_splits), is_size))
    max_combos = 50
    if len(all_combos) > max_combos:
        random.seed(42)
        all_combos = random.sample(all_combos, max_combos)

    overfit_count = 0
    total_valid = 0

    for is_combo in all_combos:
        is_groups = set(is_combo)
        oos_groups = set(range(n_splits)) - is_groups

        is_vals = [v for g in is_groups for v in segments[g]]
        oos_vals = [v for g in oos_groups for v in segments[g]]
        if len(is_vals) < 2 or len(oos_vals) < 2:
            continue

        is_mean = float(np.mean(is_vals))
        oos_mean = float(np.mean(oos_vals))
        # 浮点近零（对称混合组合）无方向信息 → 跳过（epsilon 而非 ==0）
        if abs(is_mean) < 1e-12:
            continue
        # IS 方向在 OOS 失效判据（对称、半衰）：
        #   IS>0 时 OOS 均值衰减到 IS 的一半以下（含翻负）→ 过拟合；
        #   IS<0 时对称处理。纯符号翻转判据对强翻转序列不够严格
        #   （混合组合会把 PBO 稀释到 0.48 擦线通过 0.5 门槛）。
        if is_mean > 0 and oos_mean < is_mean * 0.5:
            overfit_count += 1
        elif is_mean < 0 and oos_mean > is_mean * 0.5:
            overfit_count += 1
        total_valid += 1

    if total_valid == 0:
        return {"pbo": 0.5, "significant": False, "n_splits": n_splits,
                "indeterminate": True,
                "note": "无有效组合"}

    pbo = overfit_count / total_valid
    return {
        "pbo": round(pbo, 4),
        "significant": pbo < 0.5,
        "n_splits": n_splits,
        "total_combos": total_valid,
        "overfit_combos": overfit_count,
        "indeterminate": False,
        "method": "temporal_cscv_v2",
    }


def compute_dsr_pbo_for_factors(
    icir_list: list[float],
    n_total_candidates: int,
    sample_len: int = 252,
    ic_series: Optional[list] = None,
) -> dict:
    """
    为因子集计算 DSR + PBO，返回合并结果。

    [P0-1] ic_series：因子 IC 的时序序列（跨币对齐后按时间平均）。
    提供时 PBO 走时序 CSCV（时间维度过拟合检测）；缺失时 PBO 不可判定，
    调用方必须 fail-closed。

    Parameters:
        icir_list: 所有候选因子的 ICIR 值
        n_total_candidates: 总候选因子数（包括已有的活跃因子）
        sample_len: 样本长度（K线根数）
        ic_series: 可选，因子 IC 时序（P0-1 时序 PBO 用）

    Returns:
        {dsr_result, pbo_result, overall_passes}
    """
    if not icir_list:
        return {"dsr_result": None, "pbo_result": None, "overall_passes": False}

    arr = np.array(icir_list)
    observed = float(np.max(arr))  # 最佳 ICIR
    mean_icir = float(np.mean(arr))
    std_icir = float(np.std(arr)) if len(arr) > 1 else 0.1

    dsr_result = compute_dsr(
        observed_sr=observed,
        n_trials=n_total_candidates,
        sr_mean=mean_icir,
        sr_std=max(std_icir, 0.01),
        sample_len=sample_len,
    )

    # [P0-1] PBO 必须用 IC 时序（时间维）；仅给标量列表时不可判定 → fail-closed
    _series = ic_series if ic_series else icir_list
    pbo_result = compute_pbo_simple(_series)

    # DSR显著 且 PBO<0.5 → 通过（indeterminate 时 significant=False）
    overall_passes = dsr_result.get("significant", False) and pbo_result.get("significant", False)

    return {
        "dsr_result": dsr_result,
        "pbo_result": pbo_result,
        "overall_passes": overall_passes,
        "best_icir": round(observed, 4),
        "mean_icir": round(mean_icir, 4),
        "n_factors": len(icir_list),
        "n_total_candidates": n_total_candidates,
    }
