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
    """
    简化版 PBO 计算（基于 ICIR 分组的秩方法，不需要完整的 CPCV）。

    思路：
        1. 将 ICIR 序列按时间切分为 n_splits 段
        2. 随机选取一半作为 IS，另一半作为 OOS
        3. 对 IS 最优因子，看它在 OOS 中的排名
        4. PBO = Prob(OOS 排名 ≤ 中位数)

    Parameters:
        icir_values: 各因子的 ICIR 值列表（跨品种/时段的均值）
        n_splits: CSCV 的切分段数

    Returns:
        {pbo, significant (pbo<0.5), n_splits}
    """
    n_factors = len(icir_values)
    if n_factors < 4 or n_splits < 4:
        # 样本不足，保守估计
        return {"pbo": 0.5, "significant": False, "n_splits": n_splits,
                "note": "样本不足，返回保守值"}

    n_splits = min(n_splits, n_factors // 2)
    if n_splits < 2:
        return {"pbo": 0.5, "significant": False, "n_splits": n_splits,
                "note": "因子数太少"}

    # 将因子按 ICIR 分为 n_splits 组（模拟时序切分）
    sorted_idx = np.argsort(icir_values)
    group_size = n_factors // n_splits
    groups = [sorted_idx[i * group_size:(i + 1) * group_size] for i in range(n_splits)]

    # CSCV: 所有 C(n_splits, n_splits//2) 种 IS/OOS 划分
    from itertools import combinations
    import random

    is_size = n_splits // 2
    all_combos = list(combinations(range(n_splits), is_size))
    # 如果组合太多，随机采样
    max_combos = 50
    if len(all_combos) > max_combos:
        random.seed(42)
        all_combos = random.sample(all_combos, max_combos)

    overfit_count = 0
    total_valid = 0

    for is_combo in all_combos:
        is_groups = set(is_combo)
        oos_groups = set(range(n_splits)) - is_groups

        # 合并 IS 组的 ICIR
        is_factors = np.concatenate([groups[g] for g in is_groups])
        oos_factors = np.concatenate([groups[g] for g in oos_groups])

        if len(is_factors) < 2 or len(oos_factors) < 2:
            continue

        # IS 最优因子
        best_is_idx = is_factors[np.argmax([icir_values[i] for i in is_factors])]
        best_is_icir = icir_values[best_is_idx]

        # 该因子在 OOS 中的降序排名 r（r=1 表示 OOS 中最高）
        oos_icirs = [icir_values[i] for i in oos_factors]
        oos_rank = sum(1 for v in oos_icirs if v > best_is_icir) + 1

        # [2026-08-05 修复 PBO 方向] 学术 CSCV 定义（Bailey et al. 2015）：
        # PBO = P(R_ω* ≤ N/2)，R 为升序排名（越大越好）。等价于降序排名
        # r = N-R+1 时 PBO = P(r > N/2)，即 IS 最优因子在 OOS 中排名靠后
        # （表现差于中位）才算过拟合。此前写成 r ≤ N/2（IS 最优在 OOS 仍
        # 排名第一反而被判过拟合），方向完全相反，导致真正过拟合的因子
        # pbo≈0 通过硬门槛、稳定因子 pbo=1.0 被拦。此处按学术定义修正。
        median_rank = len(oos_factors) / 2.0
        if oos_rank > median_rank:
            overfit_count += 1
        total_valid += 1

    if total_valid == 0:
        return {"pbo": 0.5, "significant": False, "n_splits": n_splits,
                "note": "无有效组合"}

    pbo = overfit_count / total_valid
    return {
        "pbo": round(pbo, 4),
        "significant": pbo < 0.5,
        "n_splits": n_splits,
        "total_combos": total_valid,
        "overfit_combos": overfit_count,
    }


def compute_dsr_pbo_for_factors(
    icir_list: list[float],
    n_total_candidates: int,
    sample_len: int = 252,
) -> dict:
    """
    为因子集计算 DSR + PBO，返回合并结果。

    Parameters:
        icir_list: 所有候选因子的 ICIR 值
        n_total_candidates: 总候选因子数（包括已有的活跃因子）
        sample_len: 样本长度（K线根数）

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

    pbo_result = compute_pbo_simple(icir_list)

    # DSR显著 且 PBO<0.5 → 通过
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
