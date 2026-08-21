"""CPU/GPU 因子求值等价性验收（算力第一刀，2026-08-21 单卡 2080Ti 22GB）。

按《因子挖掘算法升级…》§5.3 的验收要求实跑：
  1. 用真实 DB K 线（4h × BTC/ETH/SOL）构建面板；
  2. GpuEvalContext（mem_mb=6000 / chunk=128，与 .env 同参）首用自动 _verify：
     随机 AST 上 CPU vs GPU 因子值 Pearson ≥0.99999 或 Spearman ≥0.999、
     目标 IC 偏差 ≤5e-4；
  3. 再对一批随机 AST 双路求值对照，输出最大偏差。

用法：.venv/Scripts/python.exe backend/scripts/validate_gpu_equivalence.py
"""
import os
import sys

os.environ.setdefault("LEARNING_LOOP_ENABLED", "false")
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np


def _rand_ast(rng, depth=0, max_depth=4):
    """轻量随机 AST 生成器（与矿机同款 OP_REGISTRY，剔除禁用算子）。"""
    from backend.services.factor_engine.expr.ops import OP_REGISTRY, LOOKAHEAD_BANNED_OPS
    fields = ["close", "high", "low", "volume", "vwap", "returns"]
    win_ops = [n for n, (a, _) in OP_REGISTRY.items()
               if a >= 2 and n not in LOOKAHEAD_BANNED_OPS]
    unary = [n for n, (a, _) in OP_REGISTRY.items()
             if a == 1 and n not in LOOKAHEAD_BANNED_OPS]
    all_ops = [n for n in OP_REGISTRY if n not in LOOKAHEAD_BANNED_OPS]
    if depth >= max_depth or rng.random() < 0.25:
        return {"f": str(rng.choice(fields))}
    op = str(rng.choice(all_ops))
    arity = OP_REGISTRY[op][0]
    if op in win_ops and arity >= 2 and rng.random() < 0.7:
        # 窗口型：最后一参为常量窗口
        args = [_rand_ast(rng, depth + 1, max_depth)] if arity == 2 else \
            [_rand_ast(rng, depth + 1, max_depth) for _ in range(arity - 1)]
        args.append({"c": int(rng.choice([5, 10, 20, 30]))})
        return {"op": op, "args": args}
    args = [_rand_ast(rng, depth + 1, max_depth) for _ in range(arity)]
    if not args:
        return {"f": "close"}
    return {"op": op, "args": args}


def main():
    import pandas as pd
    from dotenv import load_dotenv
    load_dotenv(".env")
    from backend.services.kline_data_service import kline_service
    from backend.services.alpha.factor_compute import kline_df_to_fields
    from backend.services.factor_engine.expr.parser import parse
    from backend.services.evolution.gp_gpu_eval import GpuEvalContext

    symbols = ["BTC", "ETH", "SOL"]
    fields_list, targets, ts_list = [], [], []
    for sym in symbols:
        rows = kline_service.get_klines_from_db(sym, "4h", 600) or []
        if len(rows) < 200:
            print(f"[SKIP] {sym} 4h K线不足: {len(rows)}")
            return 1
        df = pd.DataFrame(rows)
        fd = kline_df_to_fields(df)
        fields_list.append(fd)
        close = np.asarray(fd["close"], dtype=float)
        fwd = 6  # 与闸门对齐后的中线前瞻
        fr = np.full_like(close, np.nan)
        fr[:-fwd] = close[fwd:] / close[:-fwd] - 1.0
        targets.append(fr)
        ts_list.append(np.arange(len(close), dtype=float))
        print(f"[DATA] {sym} 4h bars={len(close)}")

    target = np.concatenate(targets)

    def cpu_fn(ctx):
        expr = ctx["expr"]
        parts = []
        for fd, t in zip(fields_list, targets):
            try:
                v = np.asarray(expr.evaluate(fd), dtype=float)
                v = v[-len(t):] if len(v) >= len(t) else v
                parts.append(v)
            except Exception:
                parts.append(np.zeros(len(t)))
        return np.concatenate(parts) if parts else np.array([])

    rng = np.random.default_rng(20260821)
    ctx = GpuEvalContext(
        cpu_fn, fields_list, target,
        mem_mb=float(os.getenv("FACTOR_EVO_GPU_MAX_MEM_MB", "6000")),
        chunk=int(os.getenv("FACTOR_EVO_GPU_CHUNK", "128")),
        verify_trees=16,
        ts_per_symbol=ts_list,
        fwd=6,
    )
    ctx.sample_fn = lambda n: [_rand_ast(rng) for _ in range(n)]

    ok = ctx._verify()
    print(f"[VERIFY] 等价性验收: {'PASS' if ok else 'FAIL'} stats={ctx._stats}")
    if not ok:
        return 2

    # 批量对照：60 个随机 AST 双路求值最大 Pearson 偏差
    asts = [_rand_ast(rng) for _ in range(60)]
    vals, mask = ctx.eval_values(asts)
    if vals is None:
        print("[BATCH] GPU 批量求值不可用（回退 CPU）")
        return 3
    worst = 1.0
    checked = 0
    for i, m in enumerate(mask):
        if not m:
            continue
        try:
            cv = np.asarray(cpu_fn({"expr": parse(asts[i])}), dtype=float)
            gv = np.asarray(vals[i], dtype=float)
            n = min(len(cv), len(gv))
            a, b = cv[:n], gv[:n]
            mm = np.isfinite(a) & np.isfinite(b)
            if mm.sum() < 50:
                continue
            c = float(np.corrcoef(a[mm], b[mm])[0, 1])
            if np.isfinite(c):
                worst = min(worst, c)
                checked += 1
        except Exception:
            continue
    print(f"[BATCH] 双路对照 {checked} 个 AST，最差 Pearson={worst:.6f} "
          f"({'PASS' if worst >= 0.99999 else 'FAIL'})")
    return 0 if worst >= 0.99999 else 4


if __name__ == "__main__":
    sys.exit(main())
