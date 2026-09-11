# -*- coding: utf-8 -*-
"""D6（盈利回撤保护）武装门槛基准修复契约测试（2026-09-10）。

根因（V4/V5 实测，12 笔 profit_drawdown_full）：
  `peak_profit` 是**残仓之前**（更大仓位）赚到的历史美元峰值，而旧口径的
  武装门槛 `3% × 当前名义` 用的是分批减仓后的残仓名义（实测 ASTER 1/8、
  UNI 1/16、BTC 1/35）→ 门槛崩塌，守卫在尘埃残仓上误触发全平；
  事件研究显示该出场后 24h 价格回归 +1.39%、72h +3.61%（出场过早）。

修复：`evaluate(..., position_value_basis=entry×original_size)`，武装门槛与
L3「翻亏全平」的 1% 名义下限都改用该基准；不传参 = 旧行为（可回滚）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from backend.services.profit_drawdown_guard import ProfitDrawdownGuard  # noqa: E402


@pytest.fixture
def guard():
    return ProfitDrawdownGuard()


def _eval(guard, **kw):
    base = dict(
        symbol="BTC", side="long", nature="trend_follow",
        entry_price=100.0, current_price=99.0,
        peak_profit=0.0, current_upnl=0.0,
        current_sl=None, position_size=5.0, tier="long",
    )
    base.update(kw)
    return guard.evaluate(**base)


def test_reduced_remnant_not_armed_with_basis(guard):
    """残仓场景：当前名义 500、原始名义 5000、峰值 100 → 新口径不武装。"""
    act = _eval(guard, peak_profit=100.0, current_upnl=-20.0, position_value_basis=5000.0)
    assert act is None, f"原始名义口径下不该武装: {act}"


def test_same_inputs_without_basis_keep_old_behavior(guard):
    """不传基准 = 旧行为（残仓名义 500 → 门槛 15 → 武装并全平），锁定可回滚。"""
    act = _eval(guard, peak_profit=100.0, current_upnl=-20.0)
    assert act is not None and act["type"] == "full_close", act


def test_genuine_profit_still_armed_and_closed(guard):
    """真有显著利润（峰值 200 ≥ 3%×5000=150）且翻亏超 1% 名义 → 仍全平。"""
    act = _eval(guard, peak_profit=200.0, current_upnl=-60.0, position_value_basis=5000.0)
    assert act is not None and act["type"] == "full_close", act


def test_flip_floor_uses_basis(guard):
    """翻亏 30 < 1%×5000=50 → 用基准后只收紧 SL（不碎平）。"""
    act = _eval(guard, peak_profit=200.0, current_upnl=-30.0, position_value_basis=5000.0)
    assert act is not None and act["type"] == "tighten_sl", act


def test_basis_none_or_zero_falls_back_to_current_value(guard):
    """basis 为 None/0 → 回退当前名义（旧口径），不抛异常。"""
    for basis in (None, 0.0):
        act = _eval(guard, peak_profit=100.0, current_upnl=-20.0, position_value_basis=basis)
        assert act is not None and act["type"] == "full_close", (basis, act)


def test_engine_passes_original_notional_basis():
    """接线契约：引擎必须传 entry×original_size 作为基准，且受开关门控。"""
    src = open(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "services", "paper_trading_engine.py",
        ),
        encoding="utf-8",
    ).read()
    assert "position_value_basis=_dd_basis" in src
    assert "original_size" in src
    assert "basis_from_original_enabled()" in src


def test_rollback_switch_reads_env(monkeypatch):
    """PDG_BASIS_ORIGINAL_NOTIONAL=false → 关闭原始名义基准（回滚旧口径）。"""
    from backend.services.profit_drawdown_guard import basis_from_original_enabled
    monkeypatch.setenv("PDG_BASIS_ORIGINAL_NOTIONAL", "false")
    assert basis_from_original_enabled() is False
    monkeypatch.setenv("PDG_BASIS_ORIGINAL_NOTIONAL", "true")
    assert basis_from_original_enabled() is True
