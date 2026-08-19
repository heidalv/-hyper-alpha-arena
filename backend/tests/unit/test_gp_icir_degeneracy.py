# -*- coding: utf-8 -*-
"""FIX-1: ICIR 单币段退化——回退 |IC|，不再爆炸到 1e8~1e9。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np

from backend.services.evolution.gp_gpu_eval import compute_fitness_from_values


def _mk_target(B=300, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, B))
    fwd = np.zeros(B)
    fwd[:-1] = close[1:] / close[:-1] - 1.0
    return fwd


def test_icir_single_segment_falls_back_to_ic():
    """仅 1 个币段有效时 ICIR 必须回退 |IC|，不得 std=0 除 1e-10 爆炸。"""
    B = 300
    t0 = _mk_target(B, 0)
    t1 = _mk_target(B, 1)
    target = np.concatenate([t0, t1])
    lens = [B, B]

    # 因子只在段 0 有限、段 1 全 NaN（真实行情下 greater/less/ts_corr 常见）
    vals = np.full((1, 2 * B), np.nan)
    sig = t0 + np.random.default_rng(2).normal(0, 0.05, B)
    vals[0, :B] = sig

    fits, case_ics = compute_fitness_from_values(
        vals,
        population=[{"f": "close"}],
        target=target,
        min_samples=50,
        lam_c=1e-3,
        lam_corr=0.05,
        elite_fvs=[],
        node_counts=[2],
        lens=lens,
        objective="icir",
        lam_hof=0.0,
        hof_values=[],
    )
    f = fits[0]
    # 有效案例段数 = 1 → 回退 |ic_full|（≈1.0，受复杂度惩罚 1e-3 影响略低）
    assert np.isfinite(f), f"fitness 应为有限值, got {f}"
    assert 0.0 < f < 3.0, f"ICIR 退化应回退 |IC| 尺度（0~3），got {f}"
    # case_ics 只有段 0 有值，段 1 为 NaN
    assert np.isfinite(case_ics[0, 0]) and np.isnan(case_ics[0, 1])


def test_icir_multi_segment_normal_scale():
    """≥2 个有效币段时 ICIR = |mean/std|，仍为正常尺度（非爆炸）。"""
    B = 300
    t0 = _mk_target(B, 0)
    t1 = _mk_target(B, 1)
    target = np.concatenate([t0, t1])
    lens = [B, B]

    rng = np.random.default_rng(3)
    # 段 0 高相关、段 1 低相关 → case_ics 有明显方差
    vals = np.full((1, 2 * B), np.nan)
    vals[0, :B] = t0 + rng.normal(0, 0.1, B)
    vals[0, B:] = t1 * 0.3 + rng.normal(0, 0.3, B)

    fits, case_ics = compute_fitness_from_values(
        vals,
        population=[{"f": "close"}],
        target=target,
        min_samples=50,
        lam_c=1e-3,
        lam_corr=0.05,
        elite_fvs=[],
        node_counts=[2],
        lens=lens,
        objective="icir",
        lam_hof=0.0,
        hof_values=[],
    )
    f = fits[0]
    assert np.isfinite(f)
    assert abs(f) < 100.0, f"ICIR 正常尺度应 <100，got {f}"
    # 两个段都应有值
    assert np.isfinite(case_ics[0, 0]) and np.isfinite(case_ics[0, 1])


def test_icir_zero_std_falls_back_to_ic():
    """段间 IC 几乎相同（std≈0）时也不应爆炸，回退 |IC|。"""
    B = 300
    t0 = _mk_target(B, 0)
    t1 = _mk_target(B, 1)
    target = np.concatenate([t0, t1])
    lens = [B, B]

    # 两段都完美线性于 target，IC 都≈1 → std≈0
    vals = np.full((1, 2 * B), np.nan)
    vals[0, :B] = t0 * 2.0
    vals[0, B:] = t1 * 2.0

    fits, _ = compute_fitness_from_values(
        vals,
        population=[{"f": "close"}],
        target=target,
        min_samples=50,
        lam_c=1e-3,
        lam_corr=0.05,
        elite_fvs=[],
        node_counts=[2],
        lens=lens,
        objective="icir",
        lam_hof=0.0,
        hof_values=[],
    )
    f = fits[0]
    assert np.isfinite(f)
    assert abs(f) < 100.0, f"std~0 的 ICIR 应回退 |IC|，got {f}"
