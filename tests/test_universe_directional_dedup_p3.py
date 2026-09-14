# -*- coding: utf-8 -*-
"""P3：universe 方向感知去重 + 成对领先-滞后冗余矩阵 单元测试。"""
import numpy as np
import pandas as pd
import pytest

from backend.services.alpha import universe_manager as um
from backend.services.coin_rank.graph_signal import pairwise_lead_matrix


def _rw_ret(n, seed, drift=0.0):
    rng = np.random.default_rng(seed)
    ret = rng.normal(drift, 0.02, size=n)
    return ret


# ─────────────────────────────────────────────────────────────
# pairwise_lead_matrix
# ─────────────────────────────────────────────────────────────
def test_pairwise_lead_matrix_detects_anchor_lead():
    anchor = _rw_ret(400, seed=1)
    rng = np.random.default_rng(7)
    follower = np.concatenate([np.zeros(2), anchor[:-2]]) + rng.normal(0, 0.002, len(anchor))
    independent = _rw_ret(400, seed=5)

    mat = pairwise_lead_matrix({"BTC": anchor, "ALT": follower, "IND": independent},
                               max_lag=6, min_bars=60)
    assert mat.loc["BTC", "ALT"] > 0.15, f"锚-跟随应显著: {mat.loc['BTC', 'ALT']:.3f}"
    assert mat.loc["BTC", "ALT"] > mat.loc["BTC", "IND"] + 0.05, \
        f"跟随对冗余应高于独立对: {mat.loc['BTC', 'IND']:.3f}"
    # 对称
    assert mat.loc["ALT", "BTC"] == mat.loc["BTC", "ALT"]


# ─────────────────────────────────────────────────────────────
# _step4 方向感知去重
# ─────────────────────────────────────────────────────────────
def _res(sym, score=1.0, status="active"):
    r = um.UniverseSymbolResult(symbol=sym, composite_score=score)
    r.status = status
    return r


def _mgr():
    mgr = um.UniverseManager()
    mgr._corr_matrix_cache = None
    mgr._lead_matrix_cache = None
    return mgr


def test_step4_directional_flag_on_rejects_lead_redundant(monkeypatch):
    """flag 开：领先-滞后冗余 > 阈值 → B 被拒（即使 |corr| 低）。"""
    monkeypatch.setattr(um, "UNIVERSE_DEDUP_DIRECTIONAL", True)
    mgr = _mgr()
    # corr 很低（不触发旧判据），lead 很高（触发新判据）
    mgr._corr_matrix_cache = pd.DataFrame([[1.0, 0.1], [0.1, 1.0]],
                                          index=["A", "B"], columns=["A", "B"])
    mgr._lead_matrix_cache = pd.DataFrame([[0.0, 0.5], [0.5, 0.0]],
                                          index=["A", "B"], columns=["A", "B"])
    out = mgr._step4_correlation_dedup([_res("A"), _res("B")])
    assert [r.symbol for r in out] == ["A"]
    assert "B" not in [r.symbol for r in out]


def test_step4_directional_flag_off_old_behavior(monkeypatch):
    """flag 关（默认）：只看 |corr|，lead 高不触发 → B 保留。"""
    monkeypatch.setattr(um, "UNIVERSE_DEDUP_DIRECTIONAL", False)
    mgr = _mgr()
    mgr._corr_matrix_cache = pd.DataFrame([[1.0, 0.1], [0.1, 1.0]],
                                          index=["A", "B"], columns=["A", "B"])
    mgr._lead_matrix_cache = pd.DataFrame([[0.0, 0.5], [0.5, 0.0]],
                                          index=["A", "B"], columns=["A", "B"])
    out = mgr._step4_correlation_dedup([_res("A"), _res("B")])
    assert [r.symbol for r in out] == ["A", "B"]


def test_step4_corr_still_works_when_directional_on(monkeypatch):
    """flag 开：旧 |corr| 判据仍然生效。"""
    monkeypatch.setattr(um, "UNIVERSE_DEDUP_DIRECTIONAL", True)
    mgr = _mgr()
    mgr._corr_matrix_cache = pd.DataFrame([[1.0, 0.9], [0.9, 1.0]],
                                          index=["A", "B"], columns=["A", "B"])
    mgr._lead_matrix_cache = None  # lead 矩阵失败 → 降级纯对称
    out = mgr._step4_correlation_dedup([_res("A"), _res("B")])
    assert [r.symbol for r in out] == ["A"]


def test_step4_no_matrices_keeps_all(monkeypatch):
    monkeypatch.setattr(um, "UNIVERSE_DEDUP_DIRECTIONAL", True)
    mgr = _mgr()
    out = mgr._step4_correlation_dedup([_res("A"), _res("B")])
    assert [r.symbol for r in out] == ["A", "B"]
