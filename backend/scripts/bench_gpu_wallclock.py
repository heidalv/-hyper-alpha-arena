"""GPU 墙钟对照验收（算力第一刀，2026-08-21 单卡 2080Ti 22GB）。

设计 §5.3 验收第三条：单轮墙钟是否下降。同一数据/种子/配置下
GP（pop=120、gens=5、单种子）分别以 GPU 上下文与纯 CPU loky 跑一轮，
输出各自墙钟与代际适应度对照。等价性（第一条）已由
validate_gpu_equivalence.py 验收通过（min_pearson=1.000000）。

用法：.venv/Scripts/python.exe backend/scripts/bench_gpu_wallclock.py
"""
import os
import sys
import time

os.environ.setdefault("LEARNING_LOOP_ENABLED", "false")
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np


def build_panel():
    import pandas as pd
    from dotenv import load_dotenv
    load_dotenv(".env")
    from backend.services.kline_data_service import kline_service
    from backend.services.alpha.factor_compute import kline_df_to_fields

    fds, targets = [], []
    for sym in ("BTC", "ETH", "SOL"):
        rows = kline_service.get_klines_from_db(sym, "4h", 800) or []
        if len(rows) < 300:
            raise SystemExit(f"{sym} 4h 数据不足")
        fd = kline_df_to_fields(pd.DataFrame(rows))
        fds.append(fd)
        close = np.asarray(fd["close"], dtype=float)
        fwd = 6
        fr = np.full_like(close, np.nan)
        fr[:-fwd] = close[fwd:] / close[:-fwd] - 1.0
        targets.append(fr)
    return fds, targets


def run_once(fds, targets, use_gpu, seed=20260821):
    from backend.services.evolution.gp_miner import GPMiner, GPConfig
    from backend.services.evolution.alpha_miner import AlphaPool
    from backend.services.evolution.gp_gpu_eval import GpuEvalContext

    target = np.concatenate(targets)

    def cpu_fn(ctx):
        expr = ctx["expr"]
        parts = []
        for fd, t in zip(fds, targets):
            try:
                v = np.asarray(expr.evaluate(fd), dtype=float)
                parts.append(v[-len(t):] if len(v) >= len(t) else np.zeros(len(t)))
            except Exception:
                parts.append(np.zeros(len(t)))
        return np.concatenate(parts) if parts else np.array([])

    cfg = GPConfig(
        population_size=120, generations=5, n_seeds=1,
        seed_values=[seed], max_workers=4,
    )
    gpu_ctx = None
    if use_gpu:
        gpu_ctx = GpuEvalContext(
            cpu_fn, fds, target,
            mem_mb=float(os.getenv("FACTOR_EVO_GPU_MAX_MEM_MB", "6000")),
            chunk=int(os.getenv("FACTOR_EVO_GPU_CHUNK", "128")),
            verify_trees=16,
            fwd=6,
        )
        gpu_ctx.sample_fn = lambda n: [
            GPMiner.__new__(GPMiner)  # 占位，实际采样在 mine() 内部
            for _ in range(0)
        ] or _default_sample(gpu_ctx, n)

    pool = AlphaPool()
    miner = GPMiner(list(fds[0].keys()), cpu_fn, target, pool, cfg, gpu_ctx=gpu_ctx)
    t0 = time.perf_counter()
    admitted = miner.mine()
    dt = time.perf_counter() - t0
    return dt, len(admitted or [])


def _default_sample(ctx, n):
    """verify 用随机 AST 采样（与 validate_gpu_equivalence 同款生成器）。"""
    from backend.services.factor_engine.expr.ops import OP_REGISTRY, LOOKAHEAD_BANNED_OPS
    rng = np.random.default_rng(7)
    out = []
    fields = list(ctx._fields[0].keys())
    win_ops = [k for k, (a, _) in OP_REGISTRY.items() if a >= 2 and k not in LOOKAHEAD_BANNED_OPS]

    def gen(d=0):
        if d >= 3 or rng.random() < 0.3:
            return {"f": str(rng.choice(fields))}
        op = str(rng.choice([k for k in OP_REGISTRY if k not in LOOKAHEAD_BANNED_OPS]))
        arity = OP_REGISTRY[op][0]
        if op in win_ops and rng.random() < 0.7:
            args = [gen(d + 1) for _ in range(max(1, arity - 1))]
            args.append({"c": int(rng.choice([5, 10, 20]))})
            return {"op": op, "args": args}
        args = [gen(d + 1) for _ in range(arity)]
        return {"op": op, "args": args or [{"f": "close"}]}

    for _ in range(n):
        out.append(gen())
    return out


def main():
    fds, targets = build_panel()
    print("[BENCH] 面板: 3 币 × 4h × 800 根；pop=120 gens=5 seed=20260821")

    dt_cpu, n_cpu = run_once(fds, targets, use_gpu=False)
    print(f"[BENCH] CPU(loky×4)  墙钟={dt_cpu:.1f}s 晋升={n_cpu}")

    dt_gpu, n_gpu = run_once(fds, targets, use_gpu=True)
    print(f"[BENCH] GPU(6000MB)  墙钟={dt_gpu:.1f}s 晋升={n_gpu}")

    speedup = dt_cpu / dt_gpu if dt_gpu > 0 else float("inf")
    verdict = "下降(通过)" if dt_gpu < dt_cpu else "未下降(保持CPU)"
    print(f"[BENCH] 结论: GPU/CPU={dt_gpu/dt_cpu:.2f}x（加速 {speedup:.2f}x）→ {verdict}")


if __name__ == "__main__":
    main()
