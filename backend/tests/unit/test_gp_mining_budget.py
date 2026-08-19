# -*- coding: utf-8 -*-
"""FIX-3: GP 挖掘硬预算截断——耗尽即停，不抛异常，保留已完成候选。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np

from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_miner import GPConfig, GPMiner


def _miner():
    cfg = GPConfig(population_size=20, generations=5, n_seeds=2, max_workers=1)
    target = np.zeros(200, dtype=float)
    miner = GPMiner(
        ["open", "high", "low", "close", "volume"],
        lambda ctx: np.zeros(200),
        target,
        AlphaPool(capacity=10),
        cfg,
    )
    return miner


def test_mine_budget_already_exhausted_returns_empty():
    """预算截止时间已在过去 → 不跑任何种子，返回空（不抛异常、不死循环）。"""
    miner = _miner()
    admitted = miner.mine(budget_deadline=time.time() - 10)
    assert admitted == []


def test_run_seed_budget_exhausted_returns_empty():
    """单种子在预算截止前不评估，直接返回空候选。"""
    miner = _miner()
    out = miner._run_seed(0, warm_start_seeds=None, budget_deadline=time.time() - 10)
    assert out == []
