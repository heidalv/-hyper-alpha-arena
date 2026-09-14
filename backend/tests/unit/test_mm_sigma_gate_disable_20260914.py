# -*- coding: utf-8 -*-
"""[2026-09-14 F82] sigma 闸禁用契约。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    LaneRiskLimits,
    check_side_allowed,
)


def test_sigma_gate_fires_when_positive():
    """默认（1.5）下 sigma 超阈值 ⇒ 封侧（旧行为不变）。"""
    ok, why = check_side_allowed(
        symbol="BTC", side="buy", book=InventoryBook(), marks={"BTC": 100.0},
        equity=5000.0, add_notional=100.0, limits=LaneRiskLimits(),
        sigma_norm=2.0,
    )
    assert not ok and "vol_pause" in why


def test_sigma_gate_disabled_when_zero():
    """vol_pause_sigma=0 ⇒ 显式禁用 sigma 闸（F82）。"""
    ok, why = check_side_allowed(
        symbol="BTC", side="buy", book=InventoryBook(), marks={"BTC": 100.0},
        equity=5000.0, add_notional=100.0,
        limits=LaneRiskLimits(vol_pause_sigma=0.0), sigma_norm=99.0,
    )
    assert ok, why


def test_sigma_gate_reduce_side_not_blocked_when_disabled():
    """禁用后减仓侧照常放行（regression guard）。"""
    book = InventoryBook()
    from backend.services.market_maker.core import Position
    book.positions["BTC"] = Position(qty=0.1, avg_px=100.0, avg_mid=100.0)
    ok, why = check_side_allowed(
        symbol="BTC", side="sell", book=book, marks={"BTC": 100.0},
        equity=5000.0, add_notional=100.0,
        limits=LaneRiskLimits(vol_pause_sigma=0.0), sigma_norm=99.0,
    )
    assert ok, why
