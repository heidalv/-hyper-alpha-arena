# -*- coding: utf-8 -*-
"""E1 核心规则（backend/services/trend_core.py）与回测口径的纯逻辑单测（不连库）。

覆盖：
  - ema_stack 入场信号定义
  - 状态机：入场 / Chandelier 出场 / 规则失效出场 / 同 bar 先出后不进 / 止损只上移
  - 权重：等权 12.5%、vol-target 帽、单笔风险帽、总敞口帽、max_positions
  - size_one 与 target_weights 一致
  - 与 v2 参考实现（haa_bt 脚本逐 bar 循环）的等价性（随机数据）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services.trend_core import (
    TrendRules, indicators, entry_signal, run_state_machine, target_weights, size_one, core_symbols,
    portfolio_vol_weights,
)


def _df(closes, highs=None, lows=None):
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float) if highs is not None else c * 1.01
    l = np.asarray(lows, dtype=float) if lows is not None else c * 0.99
    idx = pd.date_range("2020-01-01", periods=len(c), freq="D", tz="UTC")
    return pd.DataFrame({"open": c, "high": h, "low": l, "close": c}, index=idx)


def _reference_state(close: np.ndarray, sig: np.ndarray, atr: np.ndarray, mult: float) -> np.ndarray:
    """v2 回测脚本的逐 bar 循环（p[i] = 处理完 bar i-1 后的状态）→ 转成 state[t]（处理完 bar t）。"""
    n = len(close)
    p = np.zeros(n + 1)
    hi = 0.0
    inpos = False
    for i in range(1, n + 1):
        if inpos:
            hi = max(hi, close[i - 1])
            stop = hi - mult * (atr[i - 1] if not np.isnan(atr[i - 1]) else 0.0)
            if close[i - 1] < stop or sig[i - 1] == 0:
                inpos = False
        else:
            if sig[i - 1] == 1 and not np.isnan(close[i - 1]):
                inpos = True
                hi = close[i - 1]
        p[i] = 1.0 if inpos else 0.0
    return p[1:]


def test_core_symbols_default_and_env(monkeypatch):
    monkeypatch.delenv("TREND_CORE_SYMBOLS", raising=False)
    assert core_symbols() == ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX"]
    monkeypatch.setenv("TREND_CORE_SYMBOLS", "btcusdt, eth/usdt, SOL-PERP, btc")
    assert core_symbols() == ["BTC", "ETH", "SOL"]


def test_ema_stack_signal_definition():
    # 单调上涨 → 所有 EMA 多头排列且 close > EMA200 → 信号 True（在足够历史后）
    n = 400
    df = _df(100 * np.exp(np.linspace(0, 1.0, n)))
    r = TrendRules()
    ind = indicators(df, r)
    sig = entry_signal(df, r, ind)
    assert bool(sig.iloc[-1]) is True
    # 单调下跌 → False
    df2 = _df(100 * np.exp(-np.linspace(0, 1.0, n)))
    assert bool(entry_signal(df2, r).iloc[-1]) is False
    # 手工核对最后一根
    last = ind.iloc[-1]
    assert bool(sig.iloc[-1]) == bool(last["close"] > last["e_regime"] and last["e_fast"] > last["e_mid"] > last["e_slow"])


def test_state_machine_matches_v2_reference_on_random_walk():
    rng = np.random.default_rng(7)
    n = 1500
    rets = rng.normal(0.0008, 0.04, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    df = _df(close, high, low)
    r = TrendRules()
    ind = indicators(df, r)
    sig = entry_signal(df, r, ind)
    sm = run_state_machine(df, r, ind, sig)
    ref = _reference_state(close, sig.to_numpy(dtype=float), ind["atr"].to_numpy(dtype=float), r.chandelier_mult)
    assert np.array_equal(sm.state.to_numpy(), ref)
    assert sm.n_entries >= 1 and sm.n_exits >= 1
    assert sm.exit_reasons["chandelier"] + sm.exit_reasons["rule_invalid"] == sm.n_exits


def test_state_machine_chandelier_exit_and_stop_only_rises():
    # 构造：先涨（入场）再横盘再急跌打穿 3×ATR → chandelier 出场
    n = 320
    up = 100 * np.exp(np.linspace(0, 0.8, 300))
    crash = np.array([up[-1] * (1 - 0.05 * (k + 1)) for k in range(20)])  # 每天 −5%
    close = np.concatenate([up, crash])
    df = _df(close)
    r = TrendRules()
    sm = run_state_machine(df, r)
    st = sm.state.to_numpy()
    assert st[299] == 1.0
    assert st[-1] == 0.0
    assert sm.exit_reasons["chandelier"] >= 1
    # 回测口径：持仓期间 stop = 最高收盘 − 3×ATR（每日重算，可随 ATR 放大而下调）
    ind = indicators(df, r)
    held = sm.state.iloc[:300] > 0
    exp = (sm.highest_close - r.chandelier_mult * ind["atr"].fillna(0.0)).iloc[:300][held]
    assert np.allclose(sm.stop.iloc[:300][held].to_numpy(), exp.to_numpy(), equal_nan=True)
    # ratchet 口径：止损单调不降
    sm2 = run_state_machine(df, TrendRules(stop_ratchet=True))
    stops2 = sm2.stop.iloc[:300].dropna().to_numpy()
    assert np.all(np.diff(stops2) >= -1e-9)


def test_state_machine_no_reentry_same_bar_after_exit():
    # 规则一直成立但价格某天暴跌打穿 stop → 该 bar 出场且当 bar 不再入场；次 bar 若规则仍成立则重新入场
    n = 320
    base = 100 * np.exp(np.linspace(0, 0.8, n))
    close = base.copy()
    close[310] = base[309] * 0.70  # 一根 −30%
    close[311:] = base[311:] * 0.95
    df = _df(close)
    r = TrendRules()
    ind = indicators(df, r)
    sig = entry_signal(df, r, ind)
    sm = run_state_machine(df, r, ind, sig)
    st = sm.state.to_numpy()
    assert st[309] == 1.0 and st[310] == 0.0
    if bool(sig.iloc[311]):
        assert st[311] == 1.0


def test_target_weights_equal():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    state = pd.DataFrame([[1, 1, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0]], index=idx, columns=list("ABCD"), dtype=float)
    rv = pd.DataFrame(0.5, index=idx, columns=list("ABCD"))
    w = target_weights(state, rv, TrendRules(weighting="equal", max_positions=8, gross_cap=1.0))
    assert w.iloc[0].tolist() == pytest.approx([0.125, 0.125, 0, 0])
    assert w.iloc[1].sum() == pytest.approx(0.5)
    assert w.iloc[2].sum() == 0


def test_target_weights_vol_target_caps():
    idx = pd.date_range("2024-01-01", periods=1, freq="D")
    state = pd.DataFrame([[1, 1, 1]], index=idx, columns=list("ABC"), dtype=float)
    rv = pd.DataFrame([[0.35, 0.70, 1.75]], index=idx, columns=list("ABC"))  # → 1.0(cap .35) / .5(cap .35) / .2
    r = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0)
    w = target_weights(state, rv, r)
    assert w.iloc[0].tolist() == pytest.approx([0.35, 0.35, 0.20])
    # 加单笔风险帽：止损距离 10% → 1.25%/10% = 0.125 帽住前两个
    sd = pd.DataFrame([[0.10, 0.10, 0.02]], index=idx, columns=list("ABC"))
    r2 = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0, risk_per_trade_pct=0.0125)
    w2 = target_weights(state, rv, r2, stop_distance_pct=sd)
    assert w2.iloc[0].tolist() == pytest.approx([0.125, 0.125, 0.20])
    # 总敞口帽：gross 0.3 → 等比缩
    r3 = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=0.3)
    w3 = target_weights(state, rv, r3)
    assert w3.iloc[0].sum() == pytest.approx(0.3)
    assert w3.iloc[0, 0] == pytest.approx(0.35 / 0.9 * 0.3)


def test_target_weights_max_positions():
    idx = pd.date_range("2024-01-01", periods=1, freq="D")
    state = pd.DataFrame([[1, 1, 1, 1]], index=idx, columns=list("ABCD"), dtype=float)
    rv = pd.DataFrame(0.5, index=idx, columns=list("ABCD"))
    w = target_weights(state, rv, TrendRules(weighting="equal", max_positions=2, gross_cap=1.0))
    assert (w.iloc[0] > 0).sum() == 2
    assert w.iloc[0].sum() == pytest.approx(1.0)


def test_size_one_consistent_with_matrix():
    r = TrendRules(weighting="vol_target", vol_target=0.35, max_weight_per_symbol=0.35, gross_cap=1.0, risk_per_trade_pct=0.0125)
    out = size_one(10000.0, realized_vol=0.70, stop_distance_pct=0.08, rules=r)
    # vol → .5 → cap .35 → risk 0.0125/0.08 = 0.15625
    assert out["weight"] == pytest.approx(0.15625)
    assert out["notional"] == pytest.approx(1562.5)
    assert any(c.startswith("risk_per_trade") for c in out["caps"])
    assert out["risk_pct"] == pytest.approx(0.0125, abs=1e-6)
    # 总敞口帽
    out2 = size_one(10000.0, realized_vol=0.70, stop_distance_pct=0.08, rules=r, gross_open_notional=9000.0)
    assert out2["notional"] == pytest.approx(1000.0)
    assert any(c.startswith("gross_cap") for c in out2["caps"])
    # 等权退化
    out3 = size_one(10000.0, realized_vol=None, stop_distance_pct=None, rules=TrendRules())
    assert out3["weight"] == pytest.approx(0.125)


def test_portfolio_vol_weights_hits_target():
    sel = np.array([True, True, False])
    vols = np.array([0.4, 0.8, 0.6])
    cov = np.array([[0.16, 0.16, 0.0], [0.16, 0.64, 0.0], [0.0, 0.0, 0.36]])  # ρ=0.5
    w = portfolio_vol_weights(sel, vols, cov, target=0.35)
    assert w[2] == 0
    var = float(w @ cov @ w)
    assert np.sqrt(var) == pytest.approx(0.35, rel=1e-9)
    # 1/σ 比例
    assert w[0] / w[1] == pytest.approx(2.0)
