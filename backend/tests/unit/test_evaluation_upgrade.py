"""评估器升级单测（v6 计划 5.3.3）：
- quantile_backtest 分层回测
- ic_significance 单边 t 检验
- rolling_decay 滚动衰退
- parsimony_penalty 复杂度惩罚
- admission_gate 提交门槛
"""
import numpy as np
import pandas as pd

from backend.services.factor_engine.evaluation import (
    DEFAULT_GATE_CONFIG,
    admission_gate,
    ic_significance,
    parsimony_penalty,
    quantile_backtest,
    rolling_decay,
)


def _make_positively_related(n: int = 2000, seed: int = 0, signal: float = 0.3) -> tuple:
    """构造因子与未来收益正相关的合成面板（每期收益 ~1.2% 量级，净值稳定，信号强度保证分位区分度）。"""
    rng = np.random.default_rng(seed)
    sigma = 0.012
    g = rng.normal(0, 1, n)                     # 潜在收益驱动
    factor = g + rng.normal(0, 1.0, n)          # 因子观测（含噪声）
    ret = (signal * g + rng.normal(0, 1.0, n)) * sigma  # 未来收益（每期 ~1.2%）
    return pd.Series(factor), pd.Series(ret)


def test_quantile_backtest_monotone_positive():
    f, r = _make_positively_related()
    res = quantile_backtest(f, r, factor_id="t1")
    assert res.n_obs > 500
    # top 档收益 > bottom 档
    assert res.quantile_annual_ret[-1] > res.quantile_annual_ret[0]
    # 多空累计净值 > 1（正 alpha 因子）
    assert res.long_short_cum[-1] > 1.0
    assert res.long_short_sharpe > 0
    # 多头超额为正
    assert res.top_excess_cum[-1] > 1.0
    assert res.top_excess_annual > 0
    # 档位单调性为正
    assert res.monotonic_r > 0.3
    # 最大回撤非负
    assert res.top_max_drawdown >= 0.0


def test_quantile_backtest_noise_factor_no_alpha():
    rng = np.random.default_rng(1)
    n = 2000
    f = pd.Series(rng.normal(0, 1, n))
    r = pd.Series(rng.normal(0, 1, n) * 0.012)  # 纯噪声：无 alpha
    res = quantile_backtest(f, r)
    # 纯噪声因子：多头超额应接近 1（无 alpha）
    assert abs(res.top_excess_cum[-1] - 1.0) < 0.15


def test_quantile_backtest_benchmark():
    f, r = _make_positively_related()
    bench = pd.Series(np.full(len(r), 0.0005))  # 恒定正基准（0.05%/期）
    res = quantile_backtest(f, r, benchmark_returns=bench)
    assert res.top_excess_annual > 0


def test_ic_significance_positive():
    # 强正 IC 序列 → 显著（p 很小）
    rng = np.random.default_rng(2)
    ic = rng.normal(0.08, 0.02, 200)
    p = ic_significance(ic)
    assert p < 1e-6


def test_ic_significance_noise():
    rng = np.random.default_rng(3)
    ic = rng.normal(0.0, 0.05, 200)
    p = ic_significance(ic)
    assert p > 0.01  # 不显著


def test_ic_significance_short():
    assert ic_significance(np.array([0.1, 0.2])) == 1.0


def test_rolling_decay_detects_decay():
    rng = np.random.default_rng(4)
    n = 2000
    # 前半段强正相关，后半段转为负相关 → 应检出衰减
    f_strong = rng.normal(0, 1, n // 2)
    r_strong = 0.08 * f_strong + rng.normal(0, 1, n // 2)
    f_weak = rng.normal(0, 1, n // 2)
    r_weak = -0.02 * f_weak + rng.normal(0, 1, n // 2)
    f = pd.Series(np.concatenate([f_strong, f_weak]))
    r = pd.Series(np.concatenate([r_strong, r_weak]))
    out = rolling_decay(f, r, window=60, step=10)
    assert len(out["window_ics"]) >= 5
    assert out["trend_slope"] < 0
    assert out["second_half_mean_ic"] < out["first_half_mean_ic"]
    assert out["neg_streak"] >= 1


def test_rolling_decay_stable_no_decay():
    rng = np.random.default_rng(5)
    n = 2000
    g = rng.normal(0, 1, n)
    f = pd.Series(g + rng.normal(0, 1, n))
    r = pd.Series(0.05 * g + rng.normal(0, 1, n))
    out = rolling_decay(f, r, window=60, step=10)
    assert out["first_half_mean_ic"] > 0
    assert out["second_half_mean_ic"] > 0
    assert out["neg_streak"] == 0


def test_parsimony_penalty_linear():
    assert parsimony_penalty(10) == 10 * 1e-3
    assert parsimony_penalty(0) == 0.0
    assert parsimony_penalty(5, lambda_=1e-2) == 0.05
    assert parsimony_penalty(-3) == 0.0  # 负数视为 0


def test_admission_gate_pass():
    gate = admission_gate(
        top_quantile_sharpe=1.5,
        benchmark_sharpe=0.8,
        fitness=1.8,
        turnover=0.3,
        max_pool_corr=0.4,
        ic_halflife_bars=12,
        ic_mean=0.05,
        ic_p=0.001,
    )
    assert gate.passed is True
    assert gate.reasons == []


def test_admission_gate_reject_pool_corr():
    gate = admission_gate(
        top_quantile_sharpe=1.5,
        benchmark_sharpe=0.8,
        fitness=1.8,
        turnover=0.3,
        max_pool_corr=0.85,     # 池内相关性超限
        ic_halflife_bars=12,
        ic_mean=0.05,
        ic_p=0.001,
    )
    assert gate.passed is False
    assert any("max_pool_corr" in x for x in gate.reasons)


def test_admission_gate_reject_low_sharpe():
    gate = admission_gate(
        top_quantile_sharpe=0.1,
        benchmark_sharpe=0.8,
        fitness=1.8,
        turnover=0.3,
        max_pool_corr=0.2,
        ic_halflife_bars=12,
        ic_mean=0.05,
        ic_p=0.001,
    )
    assert gate.passed is False
    assert any("sharpe" in x for x in gate.reasons)


def test_admission_gate_reject_turnover_range():
    gate = admission_gate(
        top_quantile_sharpe=1.5,
        fitness=1.8,
        turnover=0.9,           # 超 70% 上限
        max_pool_corr=0.2,
        ic_halflife_bars=12,
        ic_mean=0.05,
        ic_p=0.001,
    )
    assert gate.passed is False
    assert any("turnover" in x for x in gate.reasons)


def test_admission_gate_reject_short_halflife_and_insignificant_ic():
    gate = admission_gate(
        top_quantile_sharpe=1.5,
        fitness=1.8,
        turnover=0.3,
        max_pool_corr=0.2,
        ic_halflife_bars=2,     # 半衰期太短
        ic_mean=0.01,
        ic_p=0.4,               # IC 不显著
    )
    assert gate.passed is False
    assert any("halflife" in x for x in gate.reasons)
    assert any("ic_p" in x for x in gate.reasons)


def test_admission_gate_custom_config():
    # 自定义门槛：相关性阈值收紧
    gate = admission_gate(
        top_quantile_sharpe=1.5,
        fitness=1.8,
        turnover=0.3,
        max_pool_corr=0.5,
        ic_halflife_bars=12,
        ic_mean=0.05,
        ic_p=0.001,
        config={"max_pool_corr": 0.3},
    )
    assert gate.passed is False
    assert DEFAULT_GATE_CONFIG["max_pool_corr"] == 0.7  # 默认值不被污染
