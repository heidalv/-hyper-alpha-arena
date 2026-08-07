"""
对抗验证（P4.7c，方案 §P4.7c / §4 表格）。

目标：用分类器判别 train/test 分布是否可分。若 AUC 高（分布漂移大）
→ 该模型 OOS 不可信，禁止晋升。ShadowJudge 在 SHADOW→CANARY 加此硬门。

诊断（抗过拟合表）：分布漂移是 OOS 失效的主因之一。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class AdvValidationResult:
    """对抗验证结果。"""
    auc: float                # 分类器区分 train/test 的 AUC
    is_degraded: bool         # AUC 超阈 = 分布漂移 = OOS 不可信
    threshold: float


def adversarial_validation(
    train_features: np.ndarray,
    test_features: np.ndarray,
    *,
    threshold: float = 0.55,
) -> AdvValidationResult:
    """
    对抗验证：用轻量分类器判别样本来自 train 还是 test。

    AUC ≈ 0.5 → 分布相似（OOS 可信）。
    AUC 高 → 分布漂移大（OOS 不可信，禁止晋升）。

    用 ROC-AUC 的简化计算（基于特征均值的距离），无需 sklearn。
    生产可换 sklearn LogisticRegression + cross_val AUC。
    """
    train = np.asarray(train_features, dtype=float)
    test = np.asarray(test_features, dtype=float)
    if len(train) < 10 or len(test) < 10:
        return AdvValidationResult(auc=0.5, is_degraded=False, threshold=threshold)

    # 简化：用特征均值距离的判别力近似 AUC
    # 若 train/test 均值差异大 → 分布漂移
    train_mean = np.nanmean(train, axis=0)
    test_mean = np.nanmean(test, axis=0)
    train_std = np.nanstd(train, axis=0) + 1e-9
    # 标准化均值差
    diff = np.abs(train_mean - test_mean) / train_std
    # 平均标准化差 → 映射到 [0.5, 1.0]
    mean_diff = float(np.nanmean(diff))
    # 经验映射：mean_diff 0 → AUC 0.5；mean_diff 1+ → AUC ~0.9
    auc = 0.5 + min(0.4, mean_diff * 0.4)

    return AdvValidationResult(
        auc=auc,
        is_degraded=auc > threshold,
        threshold=threshold,
    )
