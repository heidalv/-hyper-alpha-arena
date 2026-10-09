# -*- coding: utf-8 -*-
"""[P1 大轮回 2026-09-27] 一次定价契约测试（§7.2 公式逐项锁定）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.decision_core.risk_pricer import (  # noqa: E402
    lane_of,
    max_weight,
    price_once,
    risk_pct,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("P1_RISK_PCT_INTRADAY", "P1_RISK_PCT_TREND",
              "P1_MAX_WEIGHT_INTRADAY", "P1_MAX_WEIGHT_TREND",
              "MIDLONG_ONE_PRICE_MODE"):
        monkeypatch.delenv(k, raising=False)


def test_lane_mapping():
    assert lane_of("mid") == "intraday"
    assert lane_of("short") == "intraday"
    assert lane_of("long") == "trend"
    assert lane_of(None) == "intraday"


def test_risk_budget_defaults():
    assert risk_pct("intraday") == pytest.approx(0.006)
    assert risk_pct("trend") == pytest.approx(0.010)


def test_weight_caps():
    assert max_weight("intraday") == pytest.approx(0.15)
    assert max_weight("trend") == pytest.approx(0.10)


def test_notional_is_risk_over_stop():
    """§7.2 ③：名义 = 风险预算 / 止损距离。"""
    out = price_once(equity=4643.0, tier="mid", price=100.0, stop_loss=97.0, leverage=5.0)
    # risk_usd = 4643×0.6% = 27.858；stop_pct = 3%；notional = 928.6；cap = 696.45 → capped
    assert out["risk_usd"] == pytest.approx(27.86, abs=0.01)
    assert out["stop_pct"] == pytest.approx(0.03)
    assert out["capped"] is True
    assert out["notional"] == pytest.approx(696.45, abs=0.05)
    assert out["margin"] == pytest.approx(696.45 / 5.0, abs=0.05)
    assert "capped_by_weight" in out["reason"]


def test_uncapped_case_uses_formula():
    """止损距离大到 cap 不触发时，名义 = risk/stop。"""
    out = price_once(equity=4643.0, tier="mid", price=100.0, stop_loss=90.0, leverage=5.0)
    assert out["stop_pct"] == pytest.approx(0.10)
    # 27.858 / 0.10 = 278.58 < cap 696 → uncapped
    assert out["capped"] is False
    assert out["notional"] == pytest.approx(278.58, abs=0.05)


def test_trend_lane_budget():
    out = price_once(equity=4643.0, tier="long", price=100.0, stop_loss=92.0, leverage=4.0)
    # risk_usd = 46.43；stop=8% → 580.4；cap = 464.3 → capped
    assert out["risk_pct"] == pytest.approx(0.010)
    assert out["notional"] == pytest.approx(464.3, abs=0.05)


def test_degenerate_inputs_reject():
    assert price_once(equity=0, tier="mid", price=100, stop_loss=97)["reason"] == "equity<=0"
    assert price_once(equity=1000, tier="mid", price=0, stop_loss=97)["reason"] == "price<=0"
    out = price_once(equity=1000, tier="mid", price=100, stop_loss=None)
    assert out["notional"] == 0.0 and out["reason"] == "stop_pct=0"
    # floor 兜底
    out2 = price_once(equity=1000, tier="mid", price=100, stop_loss=None,
                      notional_floor_usd=60.0)
    assert out2["notional"] == 60.0


def test_leverage_never_changes_notional():
    """杠杆不动（§1.2 第3条）：只影响保证金，不影响名义。"""
    a = price_once(equity=4643.0, tier="mid", price=100.0, stop_loss=90.0, leverage=5.0)
    b = price_once(equity=4643.0, tier="mid", price=100.0, stop_loss=90.0, leverage=1.0)
    assert a["notional"] == b["notional"]
    assert b["margin"] == b["notional"]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("P1_RISK_PCT_INTRADAY", "0.01")
    monkeypatch.setenv("P1_MAX_WEIGHT_INTRADAY", "0.20")
    out = price_once(equity=1000.0, tier="mid", price=100.0, stop_loss=95.0, leverage=5.0)
    assert out["risk_usd"] == pytest.approx(10.0)
    assert out["notional"] == pytest.approx(200.0)  # 10/0.05=200，cap=200 相等


def test_posterior_mult_scales_risk_budget():
    """[P4 §11.2] 学习后验乘子 m∈[0.5,1.5] 作用在风险预算上（唯一学习乘子）。

    用 10% 止损距离保证所有乘子档都远离单币权重上限（cap 不参与本用例）。
    """
    base = price_once(equity=1000.0, tier="mid", price=100.0, stop_loss=90.0, leverage=5.0)
    assert base["capped"] is False
    half = price_once(equity=1000.0, tier="mid", price=100.0, stop_loss=90.0, leverage=5.0,
                      posterior_mult=0.5)
    one5 = price_once(equity=1000.0, tier="mid", price=100.0, stop_loss=90.0, leverage=5.0,
                      posterior_mult=1.5)
    assert half["notional"] == pytest.approx(base["notional"] * 0.5)
    assert one5["notional"] == pytest.approx(base["notional"] * 1.5)
    # 越界钳制
    wild = price_once(equity=1000.0, tier="mid", price=100.0, stop_loss=90.0,
                      leverage=5.0, posterior_mult=9.0)
    assert wild["notional"] == pytest.approx(base["notional"] * 1.5)
    assert price_once(equity=1000.0, tier="mid", price=100.0, stop_loss=90.0,
                      leverage=5.0, posterior_mult=0.01)["notional"] == pytest.approx(
                          base["notional"] * 0.5)
