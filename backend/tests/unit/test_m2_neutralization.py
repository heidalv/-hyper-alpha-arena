# -*- coding: utf-8 -*-
"""升级 v3.0 S1/M2 单测：收益中性化（beta 代理因子残差化后 IC 降幅 >50%）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import numpy as np
import pandas as pd

from backend.services.factor_engine.neutralization import build_neutralized_returns
from backend.services.factor_engine.factor_evaluator import FactorEvaluator


def _make_panel(rng, S=6, B=600):
    """合成面板：单根收益 r_t = 3*mkt_t + ε_t（市场成分主导）。

    2 根前瞻收益 ≈ r_{t+1}+r_{t+2} = 3(mkt_{t+1}+mkt_{t+2}) + 特质。
    beta 代理因子 = mkt_{t+1}+mkt_{t+2}（纯市场暴露，中性化后 IC 应归零）。
    """
    mkt = rng.normal(0, 1e-3, B)
    eps = rng.normal(0, 1e-4, (S, B))
    panels = {}
    ts0 = 1_700_000_000
    for s in range(S):
        r = 3.0 * mkt + eps[s]
        close = 100 * np.cumprod(1.0 + r)
        ts = (ts0 + np.arange(B) * 3600).astype(np.float64)
        panels[f"S{s}"] = (ts, close)
    return panels, mkt, eps


def test_neutralize_kills_market_beta():
    rng = np.random.default_rng(0)
    panels, mkt, eps = _make_panel(rng)
    fwd = 2
    neutral = build_neutralized_returns(panels, fwd)
    assert len(neutral) == len(panels)

    # 纯 beta 代理因子 = 各币公共的市场收益（因子完全跟随市场）
    ev = FactorEvaluator(forward_period=fwd)
    raw_ics, neu_ics = [], []
    beta_factor = np.concatenate([mkt[1:-1] + mkt[2:], [0.0, 0.0]])  # mkt_{t+1}+mkt_{t+2}
    for s, (ts, close) in panels.items():
        n = len(close)
        fv = pd.Series(beta_factor[:n], index=np.arange(n))
        rep_raw = ev.evaluate_factor(f"S{s}", fv, pd.Series(close, index=np.arange(n)), forward_period=fwd, neutral_returns=None)
        nr = pd.Series(neutral[s], index=np.arange(n))
        rep_neu = ev.evaluate_factor(f"S{s}", fv, pd.Series(close, index=np.arange(n)), forward_period=fwd, neutral_returns=nr)
        raw_ics.append(abs(rep_raw.ic_mean))
        neu_ics.append(abs(rep_neu.ic_mean))
    raw_avg = float(np.mean(raw_ics))
    neu_avg = float(np.mean(neu_ics))
    print(f"raw |IC|={raw_avg:.5f} neutral |IC|={neu_avg:.5f}")
    assert raw_avg > 0.2, "beta 代理因子应有显著原始 IC（测试构造有效）"
    assert neu_avg < raw_avg * 0.5, "beta 代理因子中性化后 IC 降幅应 >50%"


def test_neutralize_keeps_idiosyncratic_signal():
    rng = np.random.default_rng(7)
    panels, mkt, eps = _make_panel(rng)
    fwd = 2
    neutral = build_neutralized_returns(panels, fwd)
    ev = FactorEvaluator(forward_period=fwd)
    raws, neus = [], []
    for si, (s, (ts, close)) in enumerate(panels.items()):
        n = len(close)
        # 特质信号：只用本币特质收益（与市场正交）
        sig = np.concatenate([eps[si][1:-1] + eps[si][2:], [0.0, 0.0]])
        fv = pd.Series(sig[:n], index=np.arange(n))
        rep_raw = ev.evaluate_factor(f"S{s}", fv, pd.Series(close, index=np.arange(n)), forward_period=fwd, neutral_returns=None)
        nr = pd.Series(neutral[s], index=np.arange(n))
        rep_neu = ev.evaluate_factor(f"S{s}", fv, pd.Series(close, index=np.arange(n)), forward_period=fwd, neutral_returns=nr)
        raws.append(abs(rep_raw.ic_mean))
        neus.append(abs(rep_neu.ic_mean))
    print(f"特质信号 raw |IC|={np.mean(raws):.5f} neutral |IC|={np.mean(neus):.5f}")
    assert np.mean(neus) >= np.mean(raws) * 0.5, "特质信号中性化后 IC 不应崩掉"
