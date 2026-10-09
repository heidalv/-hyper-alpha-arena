# -*- coding: utf-8 -*-
"""[P4 大轮回 2026-09-27] §11.2 后验→行为契约测试（分层 Beta 后验 / 乘子 / 否决）。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.learning import bayes_posterior as bp  # noqa: E402


def _facts(n_wins=40, n_loss=20, net_win=2.0, net_loss=-1.5, sym="UNI",
           lane="intraday", direction="long", regime="up"):
    out = []
    for i in range(n_wins):
        out.append({"symbol": sym, "direction": direction, "lane": lane,
                    "regime": regime, "net": net_win})
    for i in range(n_loss):
        out.append({"symbol": sym, "direction": direction, "lane": lane,
                    "regime": regime, "net": net_loss})
    return out


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(bp, "load_facts", lambda db, **kw: _facts())
    monkeypatch.setattr(bp, "_avg_cost_usd", lambda db: 1.0)
    bp.invalidate_cache()


class _FakeDB:
    pass


def test_beta_stats_math():
    p = bp.posterior_for(_FakeDB(), lane="intraday", direction="long",
                         symbol="UNI", regime="up")
    # 分层先验：全局(1,1)+60 行 → (41,21)；车道先验(41,21)+60 → (81,41)；
    # 币种先验(81,41)+60 → (121,61)；regime 先验(121,61)+60 → (161,81)
    assert p["n"] == 60
    assert p["alpha"] == 161 and p["beta"] == 81
    assert p["mean_wr"] == pytest.approx(161 / 242, abs=1e-3)
    assert p["level"] == "regime", "n=60 ≥ 30 应落在 regime 桶"


def test_hierarchical_fallback_to_symbol_then_lane():
    # regime 桶只有 5 条（<30）→ 回退币种层；币种也只有 5 条 → 回退车道×方向层
    monkeypatch_facts = pytest.MonkeyPatch()
    monkeypatch_facts.setattr(bp, "load_facts",
                              lambda db, **kw: _facts(n_wins=3, n_loss=2))
    try:
        p = bp.posterior_for(_FakeDB(), lane="intraday", direction="long",
                             symbol="UNI", regime="up")
        assert p["level"] == "lane_direction"
    finally:
        monkeypatch_facts.undo()


def test_regime_prior_from_symbol_level():
    # regime 5 条、币种 60 条 → regime 回退币种层
    import types
    facts = _facts()
    reg5 = _facts(n_wins=3, n_loss=2, regime="down")
    mod = types.SimpleNamespace()
    pytest.MonkeyPatch().setattr(bp, "load_facts", lambda db, **kw: facts + reg5)
    p = bp.posterior_for(_FakeDB(), lane="intraday", direction="long",
                         symbol="UNI", regime="down")
    assert p["level"] == "symbol", "regime 桶 n<30 应回退币种层"
    assert p["n"] == 65, "币种层 = up 60 + down 5"


def test_multiplier_bounded_and_monotonic():
    db = _FakeDB()
    m_neg = bp.posterior_multiplier(db, lane="intraday", direction="long",
                                    symbol="UNI", regime="up")
    assert 0.5 <= m_neg <= 1.5
    # mean_net = (40×2 − 20×1.5)/60 = 50/60 = 0.833；cost=1 → m = 1 + 0.833/3 = 1.278
    assert m_neg == pytest.approx(1.278, abs=0.01)


def test_multiplier_clamps_at_bounds():
    db = _FakeDB()
    import pytest as _pt
    _mp = _pt.MonkeyPatch()
    _mp.setattr(bp, "load_facts",
                lambda d, **kw: _facts(n_wins=0, n_loss=50, net_loss=-20.0))
    try:
        m = bp.posterior_multiplier(db, lane="intraday", direction="long",
                                    symbol="UNI", regime="up")
        assert m == 0.5, "EV 极负时乘子钳在 0.5"
    finally:
        _mp.undo()


def test_veto_requires_sample_and_negative_ev():
    db = _FakeDB()
    blocked, why = bp.posterior_veto(db, lane="intraday", direction="long",
                                     symbol="UNI", regime="up")
    assert not blocked and "posterior_ok" in why  # mean_net=+0.83 > -3

    _mp = pytest.MonkeyPatch()
    _mp.setattr(bp, "load_facts",
                lambda d, **kw: _facts(n_wins=0, n_loss=50, net_loss=-20.0))
    try:
        blocked, why = bp.posterior_veto(db, lane="intraday", direction="long",
                                         symbol="UNI", regime="up")
        assert blocked and "posterior_veto" in why
    finally:
        _mp.undo()

    _mp2 = pytest.MonkeyPatch()
    _mp2.setattr(bp, "load_facts", lambda d, **kw: _facts(n_wins=1, n_loss=2))
    try:
        blocked, why = bp.posterior_veto(db, lane="intraday", direction="long",
                                         symbol="UNI", regime="up")
        # n=3 → 逐级回退到车道层 → 车道层不否决（P6 修复③）
        assert not blocked and "lane_level_no_veto" in why
    finally:
        _mp2.undo()


def test_veto_never_at_lane_level():
    """[P6 bug 修复③] 车道×方向兜底层不否决：否决只针对 symbol/regime 具体桶。

    生产实证：lane 层 n=363 负期望 → 旧代码否决 LINK/AAVE 等全部样本不足的币，
    等于冻结整个方向的开仓（违反 §1.2 不因怕亏压制开仓）。
    """
    db = _FakeDB()
    _mp = pytest.MonkeyPatch()
    _mp.setattr(bp, "load_facts",
                lambda d, **kw: _facts(n_wins=5, n_loss=1, net_loss=-20.0))
    try:
        # 币种层 n=6 < 30 → 回退车道层；即使车道层深负也不否决
        blocked, why = bp.posterior_veto(db, lane="intraday", direction="long",
                                         symbol="SOL", regime="up")
        assert not blocked and "lane_level_no_veto" in why
    finally:
        _mp.undo()


def test_lane_mapping_and_regime_norm():
    assert bp.lane_of("long") == "trend"
    assert bp.lane_of("mid") == "intraday"
    assert bp._norm_regime("") == "unknown"
    assert bp._norm_regime("Up") == "up"
