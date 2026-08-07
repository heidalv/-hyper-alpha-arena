"""32 线程并行回测通道加速比实测（v6 10.2.2）。

用法:
    .venv\\Scripts\\python.exe scripts/bench_parallel_backtest.py [--n 3000] [--pop 300] [--rounds 3]

口径: E5-2698B v3 16C/32T 实测，目标 32 线程 vs 1 线程加速比 >= 4-6x
（计划明确不设 10x 目标，单核偏弱）。

输出: 每档 workers 的 平均耗时(rounds 次) + 相对 1 线程加速比 + 最终判定。
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_miner import GPMiner
from backend.services.factor_engine.expr.parser import parse


def _make_fields_and_target(n: int = 3000, seed: int = 7) -> tuple:
    """合成 K 线字段 + 远期收益目标（含动量 alpha），长度贴近真实回测窗口。"""
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    volume = np.abs(rng.normal(100, 20, n)) + 10
    returns = np.diff(close, prepend=close[0]) / close[0]
    fields = {"close": close, "volume": volume, "returns": returns}
    target = np.zeros(n)
    mom = np.convolve(returns, np.ones(10) / 10, mode="same")
    target[:-5] = 0.4 * mom[:-5] + rng.normal(0, 0.008, n)[:-5]
    return fields, target


def _eval_fn_for(fields: dict):
    def _fn(ctx):
        return ctx["expr"].evaluate(fields)
    return _fn


def _make_population(miner: GPMiner, size: int) -> list:
    """随机生成 audit 通过的种群（与真实进化初始种群同口径）。"""
    rng = np.random.default_rng(42)
    population = []
    while len(population) < size:
        ast = miner._random_ast(rng, depth=0)
        if ast is None:
            continue
        if parse(ast) is not None:
            population.append(ast)
    return population


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000, help="K 线长度")
    ap.add_argument("--pop", type=int, default=300, help="种群大小（默认 300 与 GPConfig 一致）")
    ap.add_argument("--rounds", type=int, default=3, help="每档重复次数取平均")
    args = ap.parse_args()

    fields, target = _make_fields_and_target(n=args.n)
    pool = AlphaPool(capacity=10, ic_lower_bound=0.02)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool)
    population = _make_population(miner, args.pop)
    print(f"[bench] n={args.n} pop={args.pop} rounds={args.rounds} cpu={miner.config.max_workers or 32}")

    workers_list = [1, 2, 4, 8, 16, 32]
    timings: dict[int, float] = {}
    for w in workers_list:
        # warmup：先建立进程池（loky spawn 开销不计入计时；真实场景池跨代复用）
        miner._eval_population(population[:8], w)
        times = []
        for _ in range(args.rounds):
            t0 = time.perf_counter()
            miner._eval_population(population, w)
            times.append(time.perf_counter() - t0)
        timings[w] = min(times)
        print(f"[bench] workers={w:>2d}  best={timings[w]:.3f}s  avg={sum(times)/len(times):.3f}s")

    base = timings[1]
    print("\n[bench] speedup vs 1 thread:")
    for w in workers_list:
        sp = base / timings[w]
        print(f"[bench]   workers={w:>2d}  speedup={sp:.2f}x")

    sp32 = base / timings[32]
    ok = sp32 >= 4.0
    print(f"\n[bench] RESULT: 32t vs 1t speedup={sp32:.2f}x -> {'PASS (>=4x)' if ok else 'FAIL (<4x)'}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
