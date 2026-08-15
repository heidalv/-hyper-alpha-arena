#!/usr/bin/env python3
"""GPU 批量求值器基准（侧分支原型配套）。

用法（repo root）:
  .venv\\Scripts\\python.exe scripts\\bench_gpu_factor_eval.py [程序数] [树深]
  .venv\\Scripts\\python.exe scripts\\bench_gpu_factor_eval.py --equivalence
"""
import sys
import time

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from backend.services.evolution.gpu_batch_eval import (  # noqa: E402
    benchmark,
    compile_programs,
    equivalence_check,
    random_tree,
)


def main() -> None:
    import numpy as np

    if "--equivalence" in sys.argv:
        rng = np.random.default_rng(42)
        fields = np.abs(rng.normal(1.0, 0.2, (5, 9, 5000))).astype(np.float32)
        trees = [random_tree(5, seed=i) for i in range(50)]
        t0 = time.perf_counter()
        rep = equivalence_check(trees, fields)
        print("equivalence:", rep, f"({time.perf_counter()-t0:.1f}s)")
        return

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    d = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    # 真实 K 线形状合成数据（接入真实数据在 M2 里程碑）
    print(benchmark(n_programs=n, tree_depth=d))


if __name__ == "__main__":
    main()
