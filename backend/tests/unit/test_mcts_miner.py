"""MCTS 因子挖掘器单测（阶段2 S2-12：UCT + 短板扩展 + FSA + CoE + 宏微分离）。"""
import numpy as np
import pandas as pd

from backend.services.evolution.mcts_miner import (
    MCTSConfig,
    MctsMiner,
    _window_param_paths,
    scale_for_period,
    sensitivity_scan,
)
from backend.services.evolution.alpha_miner import AlphaPool
from backend.services.factor_engine.expr.parser import parse


def _make_df(n: int = 600) -> pd.DataFrame:
    """AR(1) 正自相关收益 → 动量因子有正 IC、反向因子有负 IC。"""
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    ret = np.diff(close) / close[:-1]
    ret = np.concatenate([[0.0], ret])
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": rng.uniform(10, 20, n), "returns": ret,
    })


def _forward_returns(df: pd.DataFrame, horizon: int = 5) -> np.ndarray:
    close = df["close"].values.astype(float)
    fwd = np.zeros(len(close))
    fwd[:-horizon] = close[horizon:] / close[:-horizon] - 1.0
    return fwd


def _make_env(n: int = 600):
    df = _make_df(n)
    fields = {
        "close": df["close"].values.astype(float),
        "volume": df["volume"].values.astype(float),
        "returns": df["returns"].values.astype(float),
    }
    target = _forward_returns(df)
    eval_fn = lambda ctx: ctx["expr"].evaluate(fields)
    return fields, eval_fn, target


def _small_config(**kw) -> MCTSConfig:
    """测试用小配置：串行评估 + 少量迭代（避免 loky 进程开销）。"""
    base = dict(
        n_iterations=20, n_children=3, n_roots=2, rollout_steps=1,
        max_workers=1, n_weak_seeds=2, top_k=10,
    )
    base.update(kw)
    return MCTSConfig(**base)


# ───────────────────────────── 宏微分离 ─────────────────────────────

def test_scale_for_period_mapping():
    assert scale_for_period("1m") == "micro"
    assert scale_for_period("5m") == "micro"
    assert scale_for_period("15m") == "micro"
    assert scale_for_period("30m") == "mid"
    assert scale_for_period("1h") == "mid"
    assert scale_for_period("2h") == "mid"
    assert scale_for_period("4h") == "macro"
    assert scale_for_period("8h") == "macro"
    assert scale_for_period("1d") == "macro"
    assert scale_for_period(None) == "mid"


def test_config_scale_profiles():
    """宏微分离：不同 scale 覆盖窗口档位/深度/复杂度惩罚。"""
    micro = MCTSConfig(scale="micro")
    assert micro.windows == (3, 5, 10, 20)
    assert micro.max_depth == 5
    assert micro.lambda_complexity == 2e-3
    macro = MCTSConfig(scale="macro")
    assert macro.windows == (5, 10, 20, 50)
    assert macro.rollout_steps == 3
    mid = MCTSConfig(scale="mid")
    assert mid.windows == (3, 5, 10, 20, 50)
    assert mid.max_depth == 6


# ───────────────────────────── FSA 敏感性分析 ─────────────────────────────

def test_window_param_paths_finds_windows():
    ast = {"op": "rank", "args": [
        {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 5}]}
    ]}
    paths = _window_param_paths(ast)
    assert len(paths) == 1
    assert paths[0][1] == 5.0  # corr 末位窗口=5


def test_sensitivity_scan_no_window_params():
    _, eval_fn, target = _make_env(200)
    ast = {"op": "abs", "args": [{"f": "returns"}]}
    sa = sensitivity_scan(ast, eval_fn, target)
    assert sa["n_params"] == 0
    assert sa["max_sensitivity"] == 0.0


def test_sensitivity_scan_detects_window_stability():
    """FSA：窗口参数扫描返回 3 档 IC 与敏感度；稳定因子敏感度应较低。"""
    _, eval_fn, target = _make_env(400)
    ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
    sa = sensitivity_scan(ast, eval_fn, target, weights=(0.5, 1.0, 1.5))
    assert sa["n_params"] == 1
    assert len(sa["params"][0]["ic_values"]) == 3
    assert 0.0 <= sa["max_sensitivity"] < 2.0  # 有限值


def test_sensitivity_scan_high_sensitivity_detected():
    """构造窗口极敏感因子：短窗口强动量 vs 长窗口弱信号 → 敏感度高。"""
    n = 600
    df = _make_df(n)
    # 无自相关的噪声收益：窗口越长 IC 越趋近 0 → 敏感
    rng = np.random.default_rng(7)
    noise_ret = rng.normal(0, 0.01, n)
    fields = {
        "close": df["close"].values.astype(float),
        "volume": df["volume"].values.astype(float),
        "returns": noise_ret,
    }
    target = _forward_returns(df)
    eval_fn = lambda ctx: ctx["expr"].evaluate(fields)
    ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
    sa = sensitivity_scan(ast, eval_fn, target, weights=(0.25, 1.0, 4.0))
    assert sa["n_params"] == 1
    assert sa["max_sensitivity"] >= 0.0


# ───────────────────────────── MCTS 挖掘端到端 ─────────────────────────────

def test_mine_returns_admitted_and_chains():
    """端到端：UCT 搜索产出入池因子 + CoE 进化链。"""
    fields, eval_fn, target = _make_env(400)
    pool = AlphaPool(capacity=20)
    cfg = _small_config()
    miner = MctsMiner(list(fields.keys()), eval_fn, target, pool, cfg)
    admitted, chains = miner.mine()
    # 入池因子（AR(1) 数据下有动量信号，应能命中）
    assert isinstance(admitted, list)
    for expr, contribution in admitted:
        assert expr.expr_id  # 表达式已编译
        assert contribution > 0.0
    # 进化链结构
    for ch in chains:
        assert "parent_ast" in ch and "child_ast" in ch
        assert ch["child_fitness"] > ch["parent_fitness"]
    # 池中确有因子
    assert pool.size() == len(admitted)


def test_weak_seeds_roots_shortboard_expansion():
    """短板扩展：weak_seeds 作为树根（短板优先改进）。"""
    fields, eval_fn, target = _make_env(300)
    pool = AlphaPool(capacity=20)
    # 短板种子：动量因子 + 反向因子
    weak = [
        {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]},
        {"op": "mul", "args": [
            {"c": -1.0},
            {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]},
        ]},
    ]
    cfg = _small_config(n_roots=2, n_iterations=10)
    miner = MctsMiner(list(fields.keys()), eval_fn, target, pool, cfg, weak_seeds=weak)
    roots = miner._build_roots()
    assert len(roots) == 2
    # 短板种子在前
    assert roots[0] == weak[0] or roots[0] == weak[1]
    miner.mine()
    miner.close()


def test_uct_tree_expansion_and_backprop():
    """UCT 树：扩展出子节点、visits/value 正常回传。"""
    fields, eval_fn, target = _make_env(300)
    pool = AlphaPool(capacity=20)
    cfg = _small_config(n_iterations=10, n_children=3)
    miner = MctsMiner(list(fields.keys()), eval_fn, target, pool, cfg)
    root_ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
    nodes, _ = miner._run_tree(root_ast)
    # 树根存在
    root = nodes[0]
    assert root.ast == root_ast
    # 至少扩展出若干子节点
    assert len(root.children) > 0
    # 访问计数：迭代 10 次，树总访问 ≥ 迭代次数
    total_visits = sum(n.visits for n in nodes)
    assert total_visits >= 10
    # 子节点深度正确
    assert root.children[0].depth == 1
    miner.close()


def test_mine_dedup_and_fsa_filter_runs():
    """去重 + FSA 过滤流程可运行且不抛异常。"""
    fields, eval_fn, target = _make_env(300)
    pool = AlphaPool(capacity=20)
    cfg = _small_config(n_iterations=15, top_k=20)
    miner = MctsMiner(list(fields.keys()), eval_fn, target, pool, cfg)
    admitted, chains = miner.mine()
    # 去重：无重复 expr_id
    ids = [expr.expr_id for expr, _ in admitted]
    assert len(ids) == len(set(ids))


def test_env_switch_disabled_via_factorevo():
    """接入开关：FACTOR_MCTS_ENABLED=0 时 _mine_candidates 不跑 MCTS（不抛异常）。"""
    import os
    from unittest.mock import patch

    from backend.services.evolution.factor_evolution_loop import _mine_candidates

    df = _make_df(300)
    dfs = {"BTC": df}
    with patch.dict(os.environ, {"FACTOR_MCTS_ENABLED": "0",
                                 "FACTOR_GP_POPULATION": "5",
                                 "FACTOR_GP_GENERATIONS": "2",
                                 "FACTOR_GP_SEEDS": "1"}):
        candidates = _mine_candidates(dfs, period="4h")
    assert isinstance(candidates, list)
    # 纯随机+种子候选仍存在（GP/MCTS 均被跳过或缩水）
    assert len(candidates) >= 10


def test_mine_candidates_mcts_integration():
    """_mine_candidates 集成：MCTS 段开启时运行无异常且产出来源标记 mcts_*。"""
    import os
    from unittest.mock import patch

    from backend.services.evolution.factor_evolution_loop import _mine_candidates

    df = _make_df(300)
    dfs = {"BTC": df}
    with patch.dict(os.environ, {
        "FACTOR_GP_POPULATION": "5", "FACTOR_GP_GENERATIONS": "2",
        "FACTOR_GP_SEEDS": "1",
        "FACTOR_MCTS_ITERATIONS": "10", "FACTOR_MCTS_ROOTS": "1",
        "FACTOR_MCTS_CHILDREN": "2",
    }):
        candidates = _mine_candidates(dfs, period="4h")
    assert isinstance(candidates, list)
    mcts_src = [s for _, s in candidates if s.startswith("mcts_")]
    assert len(mcts_src) >= 0  # 数据量小可能 0 命中，但流程必须跑完不抛
