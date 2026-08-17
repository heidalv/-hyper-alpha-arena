#!/usr/bin/env python3
"""GPU 批量求值器基准 — 挖矿 DSL 栈式执行器（M1.5/M2，2026-08-17 接线后）。

对比：numpy 逐树（真实 DSL parse.evaluate 路径） vs torch-CUDA 栈式批量。
用法（repo root）:
  .venv\\Scripts\\python.exe scripts\\bench_gpu_factor_eval.py [树数] [树深]
  .venv\\Scripts\\python.exe scripts\\bench_gpu_factor_eval.py --equivalence [树数]
"""
import sys
import time

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

import numpy as np  # noqa: E402

from backend.services.evolution.gpu_batch_eval import eval_panel_batch  # noqa: E402
from backend.services.evolution.gp_gpu_eval import _spearman  # noqa: E402
from backend.services.evolution.gp_miner import (  # noqa: E402
    OP_REGISTRY, LOOKAHEAD_BANNED_OPS, _CONST_VALUES, _WINDOW_VALUES,
)
from backend.services.factor_engine.expr.parser import parse  # noqa: E402

_WINDOW_OPS = {
    "ref", "mean", "sum", "std", "var", "max", "min", "ts_rank", "delta",
    "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin", "scale",
    "corr", "cov", "ts_corr",
}


def _make_panel(rng, S=5, B=1712):
    fps = []
    for _ in range(S):
        c = np.abs(rng.normal(100, 2, B))
        fps.append({
            "open": c + rng.normal(0, 0.3, B),
            "high": c + np.abs(rng.normal(1, 0.3, B)),
            "low": c - np.abs(rng.normal(1, 0.3, B)),
            "close": c + rng.normal(0, 0.5, B),
            "volume": np.abs(rng.normal(1e3, 2e2, B)) + 10,
            "returns": np.diff(c, prepend=c[0]) / c,
        })
    return fps


def _rand_ast(rng, op_names, depth):
    if depth >= 5:
        return {"f": "close"} if rng.random() < 0.7 else {"c": float(int(rng.choice(_CONST_VALUES)))}
    if depth > 0 and rng.random() < 0.35:
        return {"f": "close"} if rng.random() < 0.7 else {"c": float(int(rng.choice(_CONST_VALUES)))}
    op = str(rng.choice(op_names))
    arity, _ = OP_REGISTRY[op]
    args = []
    for i in range(arity):
        if i == arity - 1 and op in _WINDOW_OPS:
            args.append({"c": float(int(rng.choice(_WINDOW_VALUES)))})
        else:
            args.append(_rand_ast(rng, op_names, depth + 1))
    return {"op": op, "args": args}


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200
    rng = np.random.default_rng(42)
    fps = _make_panel(rng)
    op_names = [x for x in OP_REGISTRY if x not in LOOKAHEAD_BANNED_OPS]
    trees = [_rand_ast(rng, op_names, 0) for _ in range(n)]

    def eval_ref(ast):
        expr = parse(ast)
        parts = []
        for f in fps:
            try:
                parts.append(np.asarray(expr.evaluate(f), dtype=float))
            except Exception:
                parts.append(np.zeros(1712, dtype=float))
        return np.concatenate(parts)

    # GPU（含冷启动）
    t0 = time.perf_counter()
    vals, gpu_ok = eval_panel_batch(trees, fps, device="cuda")
    t_gpu_cold = time.perf_counter() - t0

    # numpy 参考（真实 DSL）
    t0 = time.perf_counter()
    refs = [eval_ref(t) for t in trees]
    t_np = time.perf_counter() - t0

    # GPU warm
    t0 = time.perf_counter()
    vals2, gpu_ok2 = eval_panel_batch(trees, fps, device="cuda")
    t_gpu_warm = time.perf_counter() - t0

    n_gpu = int(gpu_ok.sum())
    print(f"程序数: {n}, GPU 覆盖: {n_gpu}/{n} ({n_gpu/max(1,n)*100:.0f}%)")
    print(f"numpy/DSL 参考: {t_np:.2f}s ({t_np/max(1,n)*1000:.1f} ms/树)")
    print(f"GPU 冷启动(含上下文/首核): {t_gpu_cold:.2f}s")
    print(f"GPU warm: {t_gpu_warm:.3f}s ({t_gpu_warm/max(1,n_gpu)*1000:.2f} ms/树, "
          f"vs numpy {t_np/max(1,n)*1000:.1f} ms/树 → {t_np/max(t_gpu_warm,1e-9):.0f}x)")

    if "--equivalence" in sys.argv:
        bad = mism = checked = 0
        min_p = 1.0
        for i, t in enumerate(trees):
            if not gpu_ok[i]:
                continue
            r = refs[i]
            if r.shape != vals[i].shape:
                continue
            m = np.isfinite(vals[i]) & np.isfinite(r)
            if m.sum() < 20:
                continue
            gv, rv = vals[i][m], r[m]
            if np.std(gv) < 1e-12 or np.std(rv) < 1e-12:
                continue
            checked += 1
            c_p = float(np.corrcoef(gv, rv)[0, 1])
            c = _spearman(gv, rv)
            min_p = min(min_p, c_p)
            fidelity_ok = c_p >= 0.99999 or (np.isfinite(c) and c >= 0.999)
            bad += 0 if fidelity_ok else 1
            mm = float((~np.isclose(gv, rv, rtol=1e-2, atol=1e-3)).mean())
            mism += 1 if mm > 0.05 else 0
        print(f"等价性: 可比={checked} 值保真失败={bad} isclose>5%={mism} min_pearson={min_p:.6f} "
              f"→ {'通过' if checked >= 3 and bad == 0 and mism == 0 else '失败'}")


if __name__ == "__main__":
    main()
