# -*- coding: utf-8 -*-
"""[M0-C1 2026-08-23] 条件信号评价模式单测。

覆盖：条件 walk-forward 回测的触发/持有/成本语义 + 条件口径晋升路径
（反转类因子在连续口径下被拒、条件口径通过 → B 级 role=paper）。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.factor_backtest_scorer import (
    FactorBacktestScorer,
    FactorScoreResult,
)


def test_conditional_bt_reversal_direction():
    """构造确定性反转行情：超卖必反弹 → conditional(auto) 应判 dir_sign=-1 且盈利。"""
    rng = np.random.RandomState(7)
    n = 900
    ret = rng.normal(0, 0.001, n)
    factor = np.zeros(n)
    for i in range(0, n, 30):
        factor[i] = -3.0
        for j in range(1, 6):
            if i + j < n:
                factor[i + j] = -2.0
        # 触发后 fwd=6 根确定性反弹 +1.5%/根
        for j in range(1, 7):
            if i + j < n:
                ret[i + j] = 0.015
    # 未触发段因子中性
    for i in range(n):
        if factor[i] == 0.0:
            factor[i] = rng.normal(0, 0.4)
    closes = np.cumprod(1 + ret) * 100.0

    scorer = FactorBacktestScorer()
    res = scorer._conditional_walk_forward_backtest(
        factor, closes, fwd=6, cost=0.0009,
        z_threshold=2.0, direction="auto",
    )
    assert res["trades"] > 5, res
    assert res["dir_sign"] == -1.0, res
    assert res["net_return"] > 0, res
    assert res["win_rate"] >= 0.5, res


def test_conditional_bt_holds_fwd_no_overlap():
    """触发后持有 fwd 根：收益不可重叠（trades 数 ≤ 可触发根数/fwd）。"""
    rng = np.random.RandomState(3)
    n = 600
    closes = np.cumprod(1 + rng.normal(0, 0.001, n)) * 100.0
    factor = rng.normal(0, 0.5, n)
    factor[::20] = 3.0  # 每 20 根一次极端值
    scorer = FactorBacktestScorer()
    res = scorer._conditional_walk_forward_backtest(
        factor, closes, fwd=12, cost=0.0009, z_threshold=2.0, direction="momentum",
    )
    # 触发间隔 20 根 > fwd=12：每次触发都应被计入（无重叠跳过）
    assert res["trades"] >= 15, res
    assert res["triggered"] >= res["trades"], res


def test_score_formula_conditional_second_opinion(monkeypatch):
    """连续口径 C 级 + 条件口径达标 → 升级 B/conditional/admitted。

    用桩替换 _load_klines/_eval_formula/_active_factor_series，构造
    「非极端段与收益微弱负相关（连续口径打脸）、极端负值必反弹」的因子。
    """
    rng = np.random.RandomState(11)
    n = 800
    ret = rng.normal(0, 0.001, n)
    factor = np.zeros(n)
    for i in range(0, n, 40):
        factor[i] = -3.0
        for j in range(1, 5):
            if i + j < n:
                factor[i + j] = -2.0
        for j in range(1, 3):
            if i + j < n:
                ret[i + j] = 0.004  # 温和反转 alpha（fwd=2 ≈ +0.8%）
    for i in range(n):
        if factor[i] == 0.0:
            factor[i] = rng.normal(0, 0.4)
    # 非极端段与收益**零相关**（纯噪声）——连续口径在噪声段反复换仓吃成本；
    # 只有极端触发段携带温和反转 alpha（条件口径才能净赚）。
    ret = np.asarray(ret)
    closes = np.cumprod(1 + ret) * 100.0

    scorer = FactorBacktestScorer()
    scorer._load_klines = lambda sym, tf, lb: [
        {"close": float(c)} for c in closes[-lb:]
    ]
    scorer._eval_formula = lambda formula, arrays: factor[-len(arrays["close"]):]
    # 冗余检查桩：空对照集（避免真实 active 因子干扰）
    scorer._active_factor_series = lambda arrays_by_symbol, pool=None: {}

    # 注意：FactorBacktestScorer 是单例，且 test_m3_heldout 会把假 score_formula
    # 直接挂到共享实例上（历史泄漏）。这里显式取类方法本体调用，绕开实例属性遮蔽。
    _score_impl = FactorBacktestScorer.score_formula
    r = _score_impl(
        scorer,
        "ai_test_cond", "fake", symbols=["BTC", "ETH", "SOL"],
        interval="1h", lookback=700, fwd=2, cost=0.0009,
        # 显式高门槛强制连续口径 perf_ok 失败（保证条件口径路径必被触发，
        # 不受其它测试对 settings 的全局改动影响）
        min_sharpe=0.99, dsr_required=False, skip_dsr=True,
        with_conditional=True,
    )
    assert r.eval_mode == "conditional", (r.grade, r.reason)
    assert r.admitted is True
    assert r.conditional.get("trades", 0) >= 30
    assert r.conditional["dir_sign"] == -1.0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
