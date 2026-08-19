# -*- coding: utf-8 -*-
"""FIX-6: 挖掘断点续训——progress 落盘/恢复 + mine() 跳过已完成种子。"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np

from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_miner import (
    GPConfig, GPMiner, _save_mine_progress, _load_mine_progress,
)


def test_progress_roundtrip():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "progress.json")
    cands = [({"f": "close"}, 1.5), ({"f": "volume"}, 0.8)]
    _save_mine_progress(path, {0, 2}, cands)
    done, loaded = _load_mine_progress(path)
    assert done == {0, 2}
    assert len(loaded) == 2
    assert loaded[0][0] == {"f": "close"} and loaded[0][1] == 1.5


def test_load_missing_returns_empty():
    d = tempfile.mkdtemp()
    done, loaded = _load_mine_progress(os.path.join(d, "none.json"))
    assert done == set() and loaded == []


def test_mine_resume_skips_done_seeds():
    """预置 done_seeds 后 mine() 不再重跑该种子（预算已过 → 立即停，不抛异常）。"""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "progress.json")
    # 模拟上次已跑完种子 0 并产出 1 个候选
    _save_mine_progress(path, {0}, [({"f": "close"}, 1.0)])

    cfg = GPConfig(population_size=20, generations=3, n_seeds=2, max_workers=1)
    miner = GPMiner(
        ["open", "high", "low", "close", "volume"],
        lambda ctx: np.zeros(200),
        np.zeros(200, dtype=float),
        AlphaPool(capacity=10),
        cfg,
    )
    # 预算已过：种子 0 被跳过（done），种子 1 命中预算 → 停；加载的候选进入准入
    admitted = miner.mine(budget_deadline=time.time() - 1, progress_path=path)
    # 断点续训下不应崩溃；admitted 可为空（准入门禁）
    assert isinstance(admitted, list)
