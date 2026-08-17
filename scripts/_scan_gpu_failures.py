# -*- coding: utf-8 -*-
"""大量采样找 GPU vs DSL 分歧树（100 棵，记录失败 AST）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json

import numpy as np

from backend.services.factor_engine.expr.parser import parse
from backend.services.evolution.gp_miner import GPConfig, GPMiner
from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_gpu_eval import GpuEvalContext, _spearman
from backend.services.evolution.gpu_batch_eval import eval_panel_batch

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


cfg = GPConfig(population_size=40, generations=1, n_seeds=1, max_workers=2)
miner = GPMiner(sorted(field_dicts[0].keys()), eval_fn, target, AlphaPool(capacity=8), cfg)

asts = []
seen = set()
while len(asts) < 100:
    a = miner._random_ast(np.random.default_rng(10000 + len(asts)), depth=0)
    if a is None:
        continue
    k = json.dumps(a, sort_keys=True)
    if k in seen:
        continue
    seen.add(k)
    asts.append(a)

def _ops(node, acc=None):
    acc = acc if acc is not None else []
    if isinstance(node, dict) and "op" in node:
        acc.append(node["op"])
        for x in node.get("args", []):
            _ops(x, acc)
    return acc


vals, gpu_ok = eval_panel_batch(asts, field_dicts, device="cuda")
bad = []
for i, a in enumerate(asts):
    if not gpu_ok[i]:
        continue
    try:
        r = np.asarray(eval_fn({"expr": parse(a)}), dtype=float)
    except Exception:
        continue
    if r.shape != vals[i].shape:
        continue
    m = np.isfinite(vals[i]) & np.isfinite(r)
    if m.sum() < 20:
        continue
    gv, rv = vals[i][m], r[m]
    if np.std(gv) < 1e-12 or np.std(rv) < 1e-12:
        continue
    c = _spearman(gv, rv)
    mm = float((~np.isclose(gv, rv, rtol=1e-2, atol=1e-3)).mean())
    if c < 0.999 or mm > 0.05:
        bad.append((i, c, mm, a))
        print(f"FAIL tree#{i} corr={c:.5f} mismatch={mm:.3f} ops={_ops(a)}")
        print(json.dumps(a, ensure_ascii=False))
print(f"gpu_ok={gpu_ok.sum()}/100, failures={len(bad)}")
