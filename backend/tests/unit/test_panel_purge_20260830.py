# -*- coding: utf-8 -*-
"""P2 面板初筛单测：多币 pooled 评估让'面板有效'的因子存活。

构造：3 个合成币，因子 = returns（可控），close 按因子方向漂移
→ 逐币 IC 均强正 → 面板初筛应放行（旧单币逻辑在'第一个币'碰巧无效时会全灭）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd
import pytest

from backend.services.evolution import factor_evolution_loop as fe


@pytest.fixture(autouse=True)
def _fwd_horizon_1(monkeypatch):
    monkeypatch.setenv("FACTOR_EVO_FWD_BARS", "1")


def _mk_df(n=140, seed=0, edge=0.004, phi=0.9):
    rng = np.random.default_rng(seed)
    f = np.zeros(n)
    for t in range(1, n):
        f[t] = phi * f[t - 1] + rng.normal()
    rets = np.zeros(n)
    rets[1:] = edge * f[:-1]
    close = 100.0 * np.cumprod(1.0 + rets)
    return pd.DataFrame({
        "open": close * (1 + 0.0001),
        "high": close * (1 + 0.0002),
        "low": close * (1 - 0.0002),
        "close": close,
        "volume": np.abs(rng.normal(1000, 100, n)) + 1.0,
    })


def test_panel_purge_survives_multisymbol_factor(monkeypatch):
    dfs = {
        "AAA": _mk_df(seed=1),
        "BBB": _mk_df(seed=2),
        "CCC": _mk_df(seed=3),
    }
    ast_dict = {"op": "mul", "args": [{"f": "returns"}, {"c": 1.0}]}
    expr = fe.__dict__ and None
    from backend.services.factor_engine.expr.parser import parse
    parsed = parse(ast_dict)
    eval_results = {
        "fac_rev_test": {"source": "test", "expr": parsed, "best_result": None},
    }
    # DSR/PBO 门外置（本测试只验证面板初筛机制本身）
    monkeypatch.setattr(
        "backend.services.factor_engine.purge_pipeline.default_dsr_pbo_gate",
        lambda survivors, **kw: (survivors, []),
    )

    survivors = fe._purge_and_select(eval_results, dfs)
    assert len(survivors) == 1, f"面板有效因子应存活, got {survivors}"


def test_panel_purge_rejects_antisymbol_factor(monkeypatch):
    """反向因子（逐币 IC 为负）应被面板初筛拒绝。"""
    dfs = {
        "AAA": _mk_df(seed=11),
        "BBB": _mk_df(seed=12),
        "CCC": _mk_df(seed=13),
    }
    ast_dict = {"op": "mul", "args": [{"f": "returns"}, {"c": -1.0}]}
    from backend.services.factor_engine.expr.parser import parse
    parsed = parse(ast_dict)
    eval_results = {
        "fac_anti_test": {"source": "test", "expr": parsed, "best_result": None},
    }
    monkeypatch.setattr(
        "backend.services.factor_engine.purge_pipeline.default_dsr_pbo_gate",
        lambda survivors, **kw: (survivors, []),
    )
    survivors = fe._purge_and_select(eval_results, dfs)
    assert len(survivors) == 0, "镜像因子不应存活"
