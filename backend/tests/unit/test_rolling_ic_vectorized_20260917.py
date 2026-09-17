"""[轮49 2026-09-17] 向量化滚动 IC 的**数值等价性 + 加速比**契约测试。

背景：py-spy 实测因子评估的唯一 CPU 热点是 `time_series_ic` → `scipy.stats.spearmanr`
（n 根 bar 调 n 次），GPU 全程闲置。本轮加向量化快路径，**必须与老路径数值一致**
（晋升门禁阈值是按老口径调出来的），故本测试的核心是等价性。

契约：
1. 快路径与逐窗 scipy **逐元素一致**（随机数据、含并列、含 NaN）；
2. 常数输入两侧都给 0.0（老路径靠 isfinite 丢弃 NaN，同值）；
3. 开关置 0/false/off 时回到老路径且结果一致；
4. 快路径确实更快（同数据加速比 > 5×）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _legacy_ics(f, r, method="spearman", step=1):
    """复刻老路径（逐窗 information_coefficient），作为等价性基准。"""
    from backend.services.factor_engine.evaluation import information_coefficient
    df = pd.DataFrame({"f": f, "r": r}).dropna()
    if len(df) < 10:
        return np.array([])
    window = min(20, len(df) // 3)
    if window < 5:
        return np.array([information_coefficient(df["f"].values, df["r"].values, method=method)])
    _step = window if int(step) <= 0 else max(1, int(step))
    _f, _r = df["f"].values, df["r"].values
    out = []
    for i in range(window, len(df) + (1 if _step > 1 else 0), _step):
        out.append(information_coefficient(_f[i - window:i], _r[i - window:i], method=method))
    return np.array(out)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("FACTOR_EVAL_ROLLING_FAST", raising=False)
    yield


def test_equivalence_random_continuous(monkeypatch):
    from backend.services.factor_engine.evaluation import time_series_ic
    rng = np.random.default_rng(20260917)
    n = 800
    f = pd.Series(rng.normal(size=n))
    r = pd.Series(0.6 * f + rng.normal(size=n))   # 有信号，避免全退化
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
    legacy = _legacy_ics(f.values, r.values)
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
    fast = time_series_ic(f, r, method="spearman", step=1)
    assert fast.shape == legacy.shape
    np.testing.assert_allclose(fast, legacy, rtol=0, atol=1e-12)


def test_equivalence_with_ties(monkeypatch):
    """并列值必须走平均秩 —— 与 spearmanr 的并列处理一致。"""
    from backend.services.factor_engine.evaluation import time_series_ic
    rng = np.random.default_rng(7)
    n = 600
    f = pd.Series(rng.integers(0, 4, size=n).astype(float))   # 大量并列
    r = pd.Series(rng.normal(size=n))
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
    legacy = _legacy_ics(f.values, r.values)
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
    fast = time_series_ic(f, r, method="spearman", step=1)
    np.testing.assert_allclose(fast, legacy, rtol=0, atol=1e-12)


def test_equivalence_with_nan(monkeypatch):
    """NaN 由 dropna 先行剔除，两侧应在同一对齐序列上一致。"""
    from backend.services.factor_engine.evaluation import time_series_ic
    rng = np.random.default_rng(11)
    n = 700
    f = rng.normal(size=n)
    r = rng.normal(size=n)
    f[::37] = np.nan
    r[::53] = np.nan
    fs, rs = pd.Series(f), pd.Series(r)
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
    legacy = _legacy_ics(fs.values, rs.values)
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
    fast = time_series_ic(fs, rs, method="spearman", step=1)
    np.testing.assert_allclose(fast, legacy, rtol=0, atol=1e-12)


def test_equivalence_nonoverlap_step(monkeypatch):
    """step>1（非重叠）与 step=0（自动取 window）两条分支也要一致。"""
    from backend.services.factor_engine.evaluation import time_series_ic
    rng = np.random.default_rng(3)
    n = 900
    f = pd.Series(rng.normal(size=n))
    r = pd.Series(rng.normal(size=n))
    for st in (5, 0, 20):
        monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
        legacy = _legacy_ics(f.values, r.values, step=st)
        monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
        fast = time_series_ic(f, r, method="spearman", step=st)
        np.testing.assert_allclose(fast, legacy, rtol=0, atol=1e-12,
                                   err_msg=f"step={st} 不一致")


def test_constant_input_gives_zero_both_paths(monkeypatch):
    """常数输入：老路径 spearmanr→NaN→isfinite→0.0；快路径分母 0→0.0。同值。"""
    from backend.services.factor_engine.evaluation import (
        information_coefficient, time_series_ic,
    )
    assert information_coefficient(np.ones(50), np.arange(50.0), method="spearman") == 0.0
    assert information_coefficient(np.arange(50.0), np.ones(50), method="spearman") == 0.0
    f = pd.Series(np.ones(200))
    r = pd.Series(np.arange(200.0))
    for flag in ("0", "1"):
        monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", flag)
        ics = time_series_ic(f, r, method="spearman", step=1)
        assert np.all(ics == 0.0), f"flag={flag} 常数输入应为全 0"


def test_pearson_unaffected(monkeypatch):
    """快路径只覆盖 spearman；pearson 路径必须逐位不变。"""
    from backend.services.factor_engine.evaluation import time_series_ic
    rng = np.random.default_rng(5)
    f = pd.Series(rng.normal(size=400))
    r = pd.Series(rng.normal(size=400))
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
    a = time_series_ic(f, r, method="pearson", step=1)
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
    b = time_series_ic(f, r, method="pearson", step=1)
    np.testing.assert_array_equal(a, b)


def test_fast_path_is_actually_faster(monkeypatch):
    """加速比必须显著 —— 否则这次改动没有意义。"""
    import time
    from backend.services.factor_engine.evaluation import time_series_ic
    rng = np.random.default_rng(99)
    n = 4000
    f = pd.Series(rng.normal(size=n))
    r = pd.Series(rng.normal(size=n))

    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
    t0 = time.perf_counter(); time_series_ic(f, r, method="spearman", step=1); t_legacy = time.perf_counter() - t0
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
    t0 = time.perf_counter(); time_series_ic(f, r, method="spearman", step=1); t_fast = time.perf_counter() - t0

    speedup = t_legacy / max(t_fast, 1e-9)
    print(f"\n[bench] n={n} legacy={t_legacy:.3f}s fast={t_fast:.4f}s speedup={speedup:.1f}x")
    assert speedup > 5.0, f"加速比仅 {speedup:.1f}x，低于 5x 门槛"


# ── [轮53] rolling_decay 的同类逐窗循环（评估阶段剩余热点）────────────────

def _legacy_decay(f, r, window, step):
    """复刻 rolling_decay 的老逐窗路径。"""
    from backend.services.factor_engine.evaluation import information_coefficient
    df = pd.DataFrame({"f": f, "r": r}).dropna()
    fv, rv = df["f"].values, df["r"].values
    ics, i = [], 0
    while i + window <= len(df):
        ics.append(information_coefficient(fv[i:i + window], rv[i:i + window], method="spearman"))
        i += step
    return np.array(ics)


def test_rolling_decay_equivalence(monkeypatch):
    """rolling_decay 快路径必须与逐窗 scipy 逐元素一致（含多个 window/step 组合）。"""
    from backend.services.factor_engine.evaluation import rolling_decay
    rng = np.random.default_rng(4242)
    n = 1200
    f = rng.normal(size=n)
    r = rng.normal(size=n)
    f[::91] = np.nan          # 含 NaN，验证 dropna 对齐
    fs, rs = pd.Series(f), pd.Series(r)
    for window, step in ((20, 7), (10, 3), (30, 1), (5, 5)):
        monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
        legacy = _legacy_decay(fs.values, rs.values, window, step)
        monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
        got = rolling_decay(fs, rs, window=window, step=step)["window_ics"]
        np.testing.assert_allclose(got, legacy, rtol=0, atol=1e-12,
                                   err_msg=f"window={window} step={step} 不一致")


def test_rolling_decay_window_below_5_falls_back(monkeypatch):
    """window<5 必须走老路径（那里 information_coefficient 的 mask.sum()<5 规则生效）。"""
    from backend.services.factor_engine.evaluation import rolling_decay
    rng = np.random.default_rng(11)
    f = pd.Series(rng.normal(size=300))
    r = pd.Series(rng.normal(size=300))
    for flag in ("0", "1"):
        monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", flag)
        ics = rolling_decay(f, r, window=4, step=2)["window_ics"]
        assert np.all(ics == 0.0), f"flag={flag}: window<5 应全为 0.0"


def test_rolling_decay_faster(monkeypatch):
    import time
    from backend.services.factor_engine.evaluation import rolling_decay
    rng = np.random.default_rng(5)
    n = 3000
    f = pd.Series(rng.normal(size=n))
    r = pd.Series(rng.normal(size=n))
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "0")
    t0 = time.perf_counter(); rolling_decay(f, r, window=20, step=7); tl = time.perf_counter() - t0
    monkeypatch.setenv("FACTOR_EVAL_ROLLING_FAST", "1")
    t0 = time.perf_counter(); rolling_decay(f, r, window=20, step=7); tf = time.perf_counter() - t0
    print(f"\n[bench-decay] legacy={tl:.3f}s fast={tf:.4f}s speedup={tl/max(tf,1e-9):.0f}x")
    assert tl / max(tf, 1e-9) > 5.0
