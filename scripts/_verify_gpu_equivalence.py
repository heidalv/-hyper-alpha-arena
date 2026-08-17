# -*- coding: utf-8 -*-
"""GPU 栈式求值器 vs 真实 DSL（parse.evaluate）等价性验收 + 性能对比。

口径（设计文档 §8 M3）：Spearman 秩相关 ≥0.999 且 isclose 不匹配 <5%；
下游适应度是 IC（排序相关），逐值小差可容忍。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from scipy import stats  # 秩相关

from backend.services.factor_engine.expr.parser import parse
from backend.services.evolution.gp_miner import (
    OP_REGISTRY, LOOKAHEAD_BANNED_OPS, _CONST_VALUES, _WINDOW_VALUES,
)
from backend.services.evolution.gpu_batch_eval import eval_panel_batch

rng = np.random.default_rng(42)
S, B = 5, 1712
fps = []
for s in range(S):
    c = np.abs(rng.normal(100, 2, B))
    fps.append({
        "open": c + rng.normal(0, 0.3, B),
        "high": c + np.abs(rng.normal(1, 0.3, B)),
        "low": c - np.abs(rng.normal(1, 0.3, B)),
        "close": c + rng.normal(0, 0.5, B),
        "volume": np.abs(rng.normal(1e3, 2e2, B)) + 10,
        "returns": np.diff(c, prepend=c[0]) / c,
    })
op_names = [n for n in OP_REGISTRY if n not in LOOKAHEAD_BANNED_OPS]
WINDOW_OPS = {
    "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank", "delta",
    "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin", "scale",
    "corr", "cov", "ts_corr",
}


def rand_ast(depth, r):
    if depth >= 5:
        return {"f": "close"} if r.random() < 0.7 else {"c": float(int(r.choice(_CONST_VALUES)))}
    if depth > 0 and r.random() < 0.35:
        return {"f": "close"} if r.random() < 0.7 else {"c": float(int(r.choice(_CONST_VALUES)))}
    op = str(r.choice(op_names))
    arity, _ = OP_REGISTRY[op]
    args = []
    for i in range(arity):
        if i == arity - 1 and op in WINDOW_OPS:
            args.append({"c": float(int(r.choice(_WINDOW_VALUES)))})
        else:
            args.append(rand_ast(depth + 1, r))
    return {"op": op, "args": args}


def ref_values(ast):
    """真实 DSL 路径：parse + 逐币 evaluate + concat（对齐 _stack_mine_panel.eval_fn）。"""
    expr = parse(ast)
    parts = []
    for f in fps:
        try:
            parts.append(np.asarray(expr.evaluate(f), dtype=float))
        except Exception:
            parts.append(np.zeros(B, dtype=float))
    try:
        return np.concatenate(parts)
    except Exception:
        return None  # 全常量树：真实 eval_fn 在 concat 处抛错 → fitness=-inf


n = 60
asts = [rand_ast(0, rng) for _ in range(n)]
print(f"随机挖矿树 {n} 棵（含 ema/corr 等的自动 CPU 兜底划分）...")

t0 = time.perf_counter()
vals, gpu_ok = eval_panel_batch(asts, fps, device="cuda")
t_gpu = time.perf_counter() - t0
print(f"GPU 批量: {t_gpu:.3f}s, GPU 覆盖 {gpu_ok.sum()}/{n}")

t0 = time.perf_counter()
refs = [ref_values(a) for a in asts]
t_ref = time.perf_counter() - t0
print(f"numpy/DSL: {t_ref:.3f}s → {t_ref/n*1000:.1f} ms/tree")

bad_corr, min_corr, mismatch_total, mism = 0, 1.0, 0, 0
checked = 0
for i in range(n):
    if not gpu_ok[i]:
        continue
    r = refs[i]
    if r is None or r.shape != vals.shape[1:]:
        continue
    g = vals[i]
    m = np.isfinite(g) & np.isfinite(r)
    if m.sum() < 20:
        continue
    gv, rv = g[m], r[m]
    if np.std(gv) < 1e-12 or np.std(rv) < 1e-12:
        continue
    checked += 1
    c = float(stats.spearmanr(gv, rv).statistic)
    min_corr = min(min_corr, c)
    if not np.isfinite(c) or c < 0.999:
        bad_corr += 1
        print(f"  ⚠️ tree#{i} 秩相关 {c:.5f}")
    mm = float((~np.isclose(gv, rv, rtol=1e-2, atol=1e-3)).mean())
    if mm > 0.05:
        mism += 1
        print(f"  ⚠️ tree#{i} isclose 不匹配 {mm:.3f}")

print(f"验收: GPU 覆盖 {int(gpu_ok.sum())} (可比 {checked}), 秩相关 <0.999: {bad_corr}, isclose>5%: {mism}, min_corr={min_corr:.6f}")
print("EQUIVALENCE OK" if bad_corr == 0 and mism == 0 else "EQUIVALENCE FAIL")

# 性能重测（warm）
t0 = time.perf_counter()
vals2, ok2 = eval_panel_batch(asts, fps, device="cuda")
t_warm = time.perf_counter() - t0
print(f"GPU 批量 warm: {t_warm:.3f}s → {t_warm/max(1,gpu_ok.sum())*1000:.2f} ms/tree (vs numpy {t_ref/n*1000:.1f} ms/tree)")
