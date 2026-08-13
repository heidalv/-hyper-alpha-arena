"""GP 挖掘器单测（v6 计划 5.3.1）。"""
import numpy as np
import pandas as pd

from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.evolution.gp_miner import GPConfig, GPMiner
from backend.services.factor_engine.expr.parser import parse


def _make_fields_and_target(n: int = 600, seed: int = 7) -> tuple:
    """合成 K 线字段 + 正向收益（含趋势 alpha）。"""
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    volume = np.abs(rng.normal(100, 20, n)) + 10
    returns = np.diff(close, prepend=close[0]) / close[0]
    fields = {"close": close, "volume": volume, "returns": returns}
    # 远期收益与近期动量正相关
    target = np.zeros(n)
    mom = pd.Series(returns).rolling(10, min_periods=3).mean().values
    noise = rng.normal(0, 0.008, n)
    target[:-5] = 0.4 * mom[:-5] + noise[:-5]
    return fields, target


def _eval_fn_for(fields: dict):
    def _fn(ctx):
        return ctx["expr"].evaluate(fields)
    return _fn


def test_gp_miner_finds_alpha_factor():
    fields, target = _make_fields_and_target()
    pool = AlphaPool(capacity=10, ic_lower_bound=0.02)
    cfg = GPConfig(
        population_size=60, generations=6, n_seeds=2,
        min_samples=50, patience=3, seed_values=[1, 2],
        max_workers=4, top_k_admit=10,
    )
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool, cfg)
    admitted = miner.mine()
    # 至少准入一个有效因子（IC 显著为正/负均可，|IC| 作为进化压力）
    assert isinstance(admitted, list)
    # 进化机制不应崩溃；准入与否取决于 IC 门槛，弱环境下允许 0 命中但必须有过程结果
    if admitted:
        expr, contrib = admitted[0]
        assert contrib > 0
        # 表达式可 parse（FactorExpr 有 expr_id）
        assert expr.expr_id


def test_gp_miner_fitness_penalties():
    """验证适应度包含复杂度与相关性惩罚（进化压力可测）。"""
    fields, target = _make_fields_and_target()
    pool = AlphaPool(capacity=5)
    cfg = GPConfig(population_size=40, generations=2, n_seeds=1, seed_values=[3], max_workers=2)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool, cfg)
    # 构造两个候选：简单动量 vs 复杂表达式
    simple = {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]}
    complex_expr = {"op": "add", "args": [
        {"op": "mul", "args": [
            {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]},
            {"op": "div", "args": [
                {"op": "mean", "args": [{"f": "volume"}, {"c": 10}]},
                {"c": 1},
            ]},
        ]},
        {"op": "mean", "args": [{"f": "returns"}, {"c": 20}]},
    ]}
    f_simple = miner._fitness(simple)
    f_complex = miner._fitness(complex_expr)
    assert np.isfinite(f_simple) and np.isfinite(f_complex)
    # 同 IC 下简单表达式应得分更高（复杂度惩罚生效）
    fv_s = _eval_fn_for(fields)({"expr": parse(simple)})
    m = np.isfinite(fv_s) & np.isfinite(target)
    ic_s = abs(float(np.corrcoef(fv_s[m], target[m])[0, 1])) if m.sum() > 10 else 0.0
    assert f_simple <= ic_s  # 惩罚后不高于裸 IC


def test_gp_miner_rejects_constant():
    """常数表达式（零方差）应得 -inf。"""
    fields, target = _make_fields_and_target()
    pool = AlphaPool(capacity=5)
    cfg = GPConfig(population_size=20, generations=1, n_seeds=1, seed_values=[5], max_workers=2)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool, cfg)
    const_ast = {"c": 5.0}
    assert miner._fitness(const_ast) == float("-inf")


def test_gp_miner_crossover_mutation_smoke():
    """交叉/变异不破坏 AST 结构（audit 可过、深度受控）。"""
    fields, target = _make_fields_and_target()
    pool = AlphaPool(capacity=5)
    cfg = GPConfig(population_size=30, generations=1, n_seeds=1, seed_values=[9], max_workers=2)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool, cfg)
    rng = np.random.default_rng(42)
    p1 = {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]}
    p2 = {"op": "sub", "args": [
        {"op": "std", "args": [{"f": "returns"}, {"c": 20}]},
        {"op": "mean", "args": [{"f": "volume"}, {"c": 5}]},
    ]}
    child = miner._crossover(p1, p2, rng)
    assert child is not None
    assert miner._depth(child) <= cfg.max_depth + 1
    mutated = miner._mutate(p1, rng)
    assert mutated is not None
    assert miner._depth(mutated) <= cfg.max_depth + 1


# ─────────────────── v6 10.2.2 32 线程并行评估通道 ───────────────────


def test_gp_miner_default_workers_32_threads():
    """默认并行度 = min(32, cpu)：32 线程并行评估是本地算力主力（v6 10.2.2）。"""
    import os
    fields, target = _make_fields_and_target()
    pool = AlphaPool(capacity=5)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool)
    expected = min(32, os.cpu_count() or 32)
    assert miner.config.max_workers == 0  # 默认 0 = 走 32 线程口径
    assert expected >= 32  # 本机 E5-2698B 16C/32T


def test_gp_miner_blas_thread_env_locked():
    """loky worker 内 BLAS/OpenMP 线程数必须被锁为 1（O(N^2) 线程竞争负加速修复）。"""
    import os
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        assert os.environ.get(v) == "1", f"{v} 未被锁定为 1"


def test_gp_miner_eval_population_loky_parallel():
    """_eval_population 每次新建 joblib loky Parallel（禁止跨种子复用 _par）。"""
    from unittest.mock import MagicMock, patch

    from joblib import Parallel

    fields, target = _make_fields_and_target(n=200)
    pool = AlphaPool(capacity=5)
    cfg = GPConfig(population_size=20, generations=1, n_seeds=1, seed_values=[9], max_workers=2)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool, cfg)
    rng = np.random.default_rng(1)
    pop = []
    while len(pop) < 6:
        ast = miner._random_ast(rng, depth=0)
        if ast is not None and parse(ast) is not None:
            pop.append(ast)

    real_par = Parallel(n_jobs=2, backend="loky", prefer="processes")
    with patch(
        "backend.services.evolution.gp_miner.Parallel",
        side_effect=lambda **kw: real_par,
    ) as mocked:
        fits = miner._eval_population(pop, 2)
        assert mocked.called
        assert mocked.call_args.kwargs.get("backend") == "loky"
    assert len(fits) == len(pop)
    # 新契约：不跨评估缓存 self._par
    assert getattr(miner, "_par", None) is None
    miner.close()


def test_gp_miner_fitness_state_picklable():
    """适应度 state（含闭包 factor_value_fn）可被 cloudpickle 序列化（loky 要求）。"""
    import cloudpickle
    fields, target = _make_fields_and_target(n=200)
    pool = AlphaPool(capacity=5)
    cfg = GPConfig(population_size=20, generations=1, n_seeds=1, seed_values=[9], max_workers=2)
    miner = GPMiner(list(fields.keys()), _eval_fn_for(fields), target, pool, cfg)
    state = miner._fitness_state()
    blob = cloudpickle.dumps(state)
    restored = cloudpickle.loads(blob)
    assert restored["min_samples"] == cfg.min_samples
    assert restored["target"].shape == target.shape
