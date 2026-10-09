# -*- coding: utf-8 -*-
"""[P3 大轮回 2026-09-27] §6.1 波动止损标定契约：SL 距离 = max(计划, 2.2σ)，封顶 3%。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto.paper_tp_sl import finalize_open_tp_sl  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("MIDLONG_VOL_STOP_FLOOR_ENABLED", "true")
    monkeypatch.setenv("MIDLONG_SL_SIGMA_MULT", "2.2")
    monkeypatch.setenv("MIDLONG_SL_MAX_PCT", "0.03")


def _call(side="buy", price=100.0, plan_sl=None, vol=0.01):
    sl, tp = finalize_open_tp_sl(
        symbol="BTC", trade_nature="swing", side=side, price=price,
        plan_sl=plan_sl, plan_tp=None, volatility_pct=vol,
    )
    return sl


def test_tight_plan_sl_widened_to_sigma():
    """计划 SL 距离 0.5% < 2.2σ=2.2% → 加宽到 2.2%。"""
    sl = _call(plan_sl=99.5, vol=0.01)
    assert sl == pytest.approx(100.0 * (1 - 0.022), abs=1e-6)


def test_wider_structural_sl_untouched():
    """结构位 4% > 2.2σ=2.2% → 保持结构位（max 语义）。"""
    sl = _call(plan_sl=96.0, vol=0.01)
    assert sl == pytest.approx(96.0)


def test_sigma_capped_at_3pct():
    """2.2σ = 4.4% > cap 3%：2.2σ 档不生效时保持计划 SL（不强行拉到 3%）。"""
    sl = _call(plan_sl=96.0, vol=0.02)
    # sigma_dist = min(4.4%, 3%) = 3% > 计划 4% → 不动
    assert sl == pytest.approx(96.0)
    # 计划 1% < 3% cap → 拉到 3%
    sl2 = _call(plan_sl=99.0, vol=0.02)
    assert sl2 == pytest.approx(100.0 * (1 - 0.03), abs=1e-6)


def test_short_direction_symmetric():
    sl = _call(side="sell", plan_sl=100.5, vol=0.01)
    assert sl == pytest.approx(100.0 * (1 + 0.022), abs=1e-6)


def test_no_vol_leaves_sl_alone():
    sl = _call(plan_sl=99.0, vol=0)
    assert sl == pytest.approx(99.0)


def test_switch_off_rolls_back(monkeypatch):
    monkeypatch.setenv("MIDLONG_VOL_STOP_FLOOR_ENABLED", "false")
    sl = _call(plan_sl=99.5, vol=0.01)
    assert sl == pytest.approx(99.5)


def test_tp_sl_ratio_still_enforced_after_widen():
    """加宽 SL 后 TP/SL ≥ 1.3 比率校正仍生效。"""
    sl, tp = finalize_open_tp_sl(
        symbol="BTC", trade_nature="swing", side="buy", price=100.0,
        plan_sl=99.5, plan_tp=101.0, volatility_pct=0.01,
    )
    assert sl == pytest.approx(97.8, abs=1e-6)  # 2.2% 加宽
    assert (tp - 100.0) / (100.0 - sl) >= 1.3 - 1e-9


def test_wiring_passes_volatility(monkeypatch):
    """调用方必须把 volatility_pct 传入（源码契约）。"""
    src = (ROOT / "backend" / "services" / "full_auto" / "paper_execution.py"
           ).read_text(encoding="utf-8")
    assert "volatility_pct=" in src
