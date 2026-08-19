# -*- coding: utf-8 -*-
"""FIX-2: CPU 兜底路径 _fitness_core 与 GPU 路径 ICIR 口径一致。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np

from backend.services.evolution.gp_miner import _fitness_core


def _mk_target(B=300, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, B))
    fwd = np.zeros(B)
    fwd[:-1] = close[1:] / close[:-1] - 1.0
    return fwd


def test_fitness_core_icir_single_segment_falls_back():
    """CPU 路径：objective=icir 且仅 1 币段有效 → 回退 |IC|，不爆炸。"""
    B = 300
    t0 = _mk_target(B, 0)
    t1 = _mk_target(B, 1)
    target = np.concatenate([t0, t1])

    vals0 = np.full(2 * B, np.nan)
    vals0[:B] = t0 + np.random.default_rng(2).normal(0, 0.05, B)

    def eval_fn(ctx):
        return vals0

    state = {
        "factor_value_fn": eval_fn,
        "target": target,
        "min_samples": 50,
        "lambda_complexity": 1e-3,
        "lambda_corr": 0.05,
        "objective": "icir",
        "lens": [B, B],
        "elite_ast": [],
        "elite_fvs": [],
    }
    f = _fitness_core({"f": "close"}, state)
    assert np.isfinite(f) and 0.0 < f < 3.0, f"单段 ICIR 应回退 |IC| 尺度，got {f}"


def test_fitness_core_icir_multi_segment_uses_icir():
    """CPU 路径：objective=icir 且多段有效 → 用 ICIR = |mean/std|（与 GPU 一致）。"""
    B = 300
    t0 = _mk_target(B, 0)
    t1 = _mk_target(B, 1)
    target = np.concatenate([t0, t1])
    rng = np.random.default_rng(3)

    vals0 = np.full(2 * B, np.nan)
    vals0[:B] = t0 + rng.normal(0, 0.1, B)
    vals0[B:] = t1 * 0.3 + rng.normal(0, 0.3, B)

    def eval_fn(ctx):
        return vals0

    state = {
        "factor_value_fn": eval_fn,
        "target": target,
        "min_samples": 50,
        "lambda_complexity": 1e-3,
        "lambda_corr": 0.05,
        "objective": "icir",
        "lens": [B, B],
        "elite_ast": [],
        "elite_fvs": [],
    }
    f = _fitness_core({"f": "close"}, state)
    assert np.isfinite(f) and abs(f) < 100.0, f"多段 ICIR 应为正常尺度，got {f}"


def test_fitness_core_ic_objective_unchanged():
    """objective=ic（默认）行为不变：仍用 |IC|。"""
    B = 300
    t0 = _mk_target(B, 0)
    target = np.concatenate([t0, _mk_target(B, 1)])
    vals0 = np.full(2 * B, np.nan)
    vals0[:B] = t0 + np.random.default_rng(4).normal(0, 0.1, B)

    state = {
        "factor_value_fn": lambda ctx: vals0,
        "target": target,
        "min_samples": 50,
        "lambda_complexity": 1e-3,
        "lambda_corr": 0.05,
        "objective": "ic",
        "lens": None,
        "elite_ast": [],
        "elite_fvs": [],
    }
    f = _fitness_core({"f": "close"}, state)
    assert np.isfinite(f) and 0.0 < f < 3.0, f"ic 目标应为 |IC| 尺度，got {f}"
