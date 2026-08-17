# -*- coding: utf-8 -*-
"""GPU 接线端到端集成测试：小规模 GP 进化走 GPU 路径 + 与 loky 路径适应度对比。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
import numpy as np

from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_miner import GPConfig, GPMiner
from backend.services.evolution.gp_gpu_eval import GpuEvalContext
from backend.services.factor_engine.expr.parser import parse

rng = np.random.default_rng(7)
S, B = 5, 1712
field_dicts = []
targets = []
for s in range(S):
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


def eval_fn(ctx):
    expr = ctx["expr"]
    parts = []
    for fd, ln in zip(field_dicts, [len(t) for t in targets]):
        try:
            parts.append(np.asarray(expr.evaluate(fd), dtype=float))
        except Exception:
            parts.append(np.zeros(ln, dtype=float))
    return np.concatenate(parts)


field_names = sorted({k for fd in field_dicts for k in fd.keys()})
cfg = GPConfig(population_size=40, generations=1, n_seeds=1, max_workers=8)
pool = AlphaPool(capacity=80)

ctx = GpuEvalContext(
    factor_value_fn=eval_fn, fields_per_symbol=field_dicts, target=target,
    min_samples=cfg.min_samples, lambda_complexity=cfg.lambda_complexity,
    lambda_corr=cfg.lambda_corr, mem_mb=1200, chunk=64, verify_trees=10,
)

miner = GPMiner(field_names, eval_fn, target, pool, cfg, gpu_ctx=ctx)
ctx.sample_fn = lambda n: [
    a for a in (miner._random_ast(np.random.default_rng(3000 + i), depth=0) for i in range(n))
    if a is not None
]

# 生成一个固定种群，分别走 GPU 与 loky 路径比较适应度
pop = []
while len(pop) < 120:
    a = miner._random_ast(np.random.default_rng(len(pop) + 99), depth=0)
    if a is not None:
        pop.append(a)

# 首次调用：含一次性等价性验收
t0 = time.perf_counter()
fits_gpu = miner._eval_population(pop)
t_first = time.perf_counter() - t0

miner2 = GPMiner(field_names, eval_fn, target, AlphaPool(capacity=80), cfg, gpu_ctx=None)
fits_loky = miner2._eval_population(pop)

fg = np.array(fits_gpu, dtype=float)
fl = np.array(fits_loky, dtype=float)
valid = np.isfinite(fg) & np.isfinite(fl)
print(f"首次(含验收): GPU={t_first:.2f}s")
print(f"适应度有效样本: {valid.sum()}/{len(pop)}")
if valid.sum() >= 5:
    c = float(np.corrcoef(fg[valid], fl[valid])[0, 1])
    print(f"适应度 Pearson 相关: {c:.5f}")

# 稳态基准：填充精英后各跑 3 代
top = sorted(zip(pop, fits_gpu), key=lambda x: x[1], reverse=True)[:15]
miner._elite_ast = [copy.deepcopy(a) for a, _ in top if np.isfinite(_)]
miner2._elite_ast = [copy.deepcopy(a) for a, _ in top if np.isfinite(_)]
for name, m in (("GPU", miner), ("loky", miner2)):
    ts = []
    for _ in range(2):
        t0 = time.perf_counter()
        m._eval_population(pop)
        ts.append(time.perf_counter() - t0)
    print(f"{name} 稳态 {len(pop)} 树/代: {[f'{t:.2f}s' for t in ts]}")
print("stats:", ctx._stats)
