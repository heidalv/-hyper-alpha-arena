# -*- coding: utf-8 -*-
"""真实 DSL 求值成本基准：随机挖矿 AST × 合成 K 线面板。
回答：每棵树 eval 花多少时间、corrcoef 占多少、32 进程下种群一代要多久。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from backend.services.factor_engine.expr.parser import parse
from backend.services.evolution.gp_miner import OP_REGISTRY, LOOKAHEAD_BANNED_OPS, _CONST_VALUES, _WINDOW_VALUES, _SCALE_VALUES, _count_nodes, _fitness_core

S, B = 5, 1712
rng = np.random.default_rng(0)
fields_per_sym = []
for s in range(S):
    f = {}
    f["open"] = np.abs(rng.normal(100, 2, B))
    f["high"] = f["open"] + np.abs(rng.normal(1, 0.3, B))
    f["low"] = f["open"] - np.abs(rng.normal(1, 0.3, B))
    f["close"] = f["open"] + rng.normal(0, 0.5, B)
    f["volume"] = np.abs(rng.normal(1e3, 2e2, B)) + 10
    f["returns"] = np.diff(f["close"], prepend=f["close"][0]) / f["close"]
    fields_per_sym.append(f)

target = np.concatenate([rng.normal(0, 1e-3, B) for _ in range(S)])
op_names = [n for n in OP_REGISTRY if n not in LOOKAHEAD_BANNED_OPS]


def rand_ast(depth):
    if depth >= 5:
        return {"f": "close"} if rng.random() < 0.7 else {"c": float(int(rng.choice(_CONST_VALUES)))}
    if depth > 0 and rng.random() < 0.35:
        return {"f": "close"} if rng.random() < 0.7 else {"c": float(int(rng.choice(_CONST_VALUES)))}
    op = str(rng.choice(op_names))
    arity, _ = OP_REGISTRY[op]
    args = []
    for i in range(arity):
        if i == arity - 1 and op in {
            "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank", "delta",
            "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin", "scale",
            "corr", "cov", "ts_corr",
        }:
            args.append({"c": float(int(rng.choice(_WINDOW_VALUES)))})
        else:
            args.append(rand_ast(depth + 1))
    return {"op": op, "args": args}


def eval_fn(ctx):
    expr = ctx["expr"]
    parts = [np.asarray(expr.evaluate(f), dtype=float) for f in fields_per_sym]
    return np.concatenate(parts)


asts = [rand_ast(0) for _ in range(120)]
exprs = []
for a in asts:
    try:
        exprs.append(parse(a))
    except Exception:
        pass
print(f"parsed {len(exprs)}/{len(asts)} trees; avg nodes={np.mean([_count_nodes(a) for a in asts]):.1f}")

# 单树 eval 成本
t0 = time.perf_counter()
vals = []
for e in exprs:
    vals.append(eval_fn({"expr": e}))
t_eval = time.perf_counter() - t0
print(f"eval {len(exprs)} trees: {t_eval:.3f}s → {t_eval/len(exprs)*1000:.2f} ms/tree")

# fitness 完整成本（含 corrcoef + 精英惩罚）
state = {
    "factor_value_fn": eval_fn,
    "target": target,
    "min_samples": 50,
    "lambda_complexity": 1e-3,
    "lambda_corr": 0.05,
    "elite_ast": asts[:12],
}
t0 = time.perf_counter()
fits = [_fitness_core(a, state) for a in asts]
t_fit = time.perf_counter() - t0
print(f"fitness(含精英) {len(asts)} trees: {t_fit:.3f}s → {t_fit/len(asts)*1000:.2f} ms/tree")

# parse 成本
t0 = time.perf_counter()
for a in asts:
    try:
        parse(a)
    except Exception:
        pass
t_parse = time.perf_counter() - t0
print(f"parse {len(asts)} trees: {t_parse:.3f}s → {t_parse/len(asts)*1000:.2f} ms/tree")

# 一代种群规模估算（300 树 × 20 代 × 6 种子，32 workers）
per_tree = t_fit / len(asts)
total_evals = 300 * 20 * 6
print(f"估算: 全进化 {total_evals} 次 fitness × {per_tree*1000:.2f}ms / 32 workers ≈ {total_evals*per_tree/32:.1f}s")
