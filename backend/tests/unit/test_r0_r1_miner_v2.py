# -*- coding: utf-8 -*-
"""升级 v3.0 S1/R0+R1 冒烟：ε-lexicase + ICIR 目标 + 名人堂协同奖励全链路。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np

from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_miner import GPConfig, GPMiner
from backend.services.evolution.gp_gpu_eval import GpuEvalContext
from backend.services.factor_engine.expr.parser import parse


def _panel():
    rng = np.random.default_rng(11)
    S, B = 5, 800
    field_dicts = []
    targets = []
    for _ in range(S):
        c = np.abs(rng.normal(100, 2, B))
        fd = {
            "open": c + rng.normal(0, 0.3, B),
            "high": c + np.abs(rng.normal(1, 0.3, B)),
            "low": c - np.abs(rng.normal(1, 0.3, B)),
            "close": c + rng.normal(0, 0.5, B),
            "volume": np.abs(rng.normal(1e3, 2e2, B)) + 10,
            "returns": np.diff(c, prepend=c[0]) / c,
        }
        field_dicts.append(fd)
        fwd = np.zeros(B)
        fwd[:-1] = fd["close"][1:] / fd["close"][:-1] - 1
        targets.append(fwd)
    target = np.concatenate(targets)
    ts = [np.arange(B, dtype=float) * 3600 + 1_700_000_000 for _ in range(S)]

    def eval_fn(ctx):
        expr = ctx["expr"]
        parts = []
        for fd, ln in zip(field_dicts, [len(t) for t in targets]):
            try:
                parts.append(np.asarray(expr.evaluate(fd), dtype=float))
            except Exception:
                parts.append(np.zeros(ln, dtype=float))
        return np.concatenate(parts)

    return field_dicts, target, ts, eval_fn


def test_gpu_miner_v2_lexicase_icir():
    field_dicts, target, ts, eval_fn = _panel()
    cfg = GPConfig(
        population_size=30, generations=2, n_seeds=1, max_workers=2,
        selection="lexicase", objective="icir", lambda_hof=0.1,
    )
    ctx = GpuEvalContext(
        factor_value_fn=eval_fn, fields_per_symbol=field_dicts, target=target,
        min_samples=cfg.min_samples, lambda_complexity=cfg.lambda_complexity,
        lambda_corr=cfg.lambda_corr, mem_mb=1200, chunk=64, verify_trees=8,
        ts_per_symbol=ts, fwd=2,
    )
    miner = GPMiner(sorted(field_dicts[0].keys()), eval_fn, target, AlphaPool(capacity=16), cfg, gpu_ctx=ctx)
    ctx.sample_fn = lambda n: [
        a for a in (miner._random_ast(np.random.default_rng(5000 + i), depth=0) for i in range(n))
        if a is not None
    ]
    admitted = miner.mine()
    print(f"admitted={len(admitted)} case_cache={len(miner._case_cache)} hof={len(miner._hof)} "
          f"verify_ok={ctx._stats.get('verify_ok')} neutralized={ctx._neutralized}")
    assert ctx._stats.get("verify_ok"), "等价性验收应通过"
    assert len(miner._case_cache) > 0, "案例缓存应被填充（ε-lexicase 数据源）"
    # 有 GPU 覆盖且至少跑完 2 代不炸即可；admitted 可为空（小种群+门禁）
