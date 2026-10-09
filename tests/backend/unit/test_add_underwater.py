# -*- coding: utf-8 -*-
"""[h897 2026-10-07] 加仓摊平 guard(add_blocked_underwater)单测。

规则:持仓浮亏 > 阈值 ⇒ 禁止加仓(不摊平亏损);浮盈/平价/空仓 ⇒ 不拦。
"""
from __future__ import annotations

import pytest

from backend.services.market_maker.core import add_blocked_underwater


def test_long_underwater_blocked():
    # 多头成本 100,现价 99.90 ⇒ 浮亏 −10bp;阈值 8 ⇒ 拦
    assert add_blocked_underwater(1.0, 100.0, 99.90, 8.0) is True


def test_long_profit_not_blocked():
    # 多头成本 100,现价 100.05 ⇒ 浮盈 +5bp ⇒ 不拦
    assert add_blocked_underwater(1.0, 100.0, 100.05, 8.0) is False


def test_short_underwater_blocked():
    # 空头成本 100,现价 100.10 ⇒ 浮亏 −10bp ⇒ 拦
    assert add_blocked_underwater(-1.0, 100.0, 100.10, 8.0) is True


def test_short_profit_not_blocked():
    # 空头成本 100,现价 99.95 ⇒ 浮盈 +5bp ⇒ 不拦
    assert add_blocked_underwater(-1.0, 100.0, 99.95, 8.0) is False


def test_flat_or_invalid_never_blocks():
    assert add_blocked_underwater(0.0, 100.0, 99.0, 8.0) is False   # 空仓
    assert add_blocked_underwater(1.0, 0.0, 99.0, 8.0) is False     # 无成本价
    assert add_blocked_underwater(1.0, 100.0, 0.0, 8.0) is False    # 无现价


def test_disabled_when_threshold_zero():
    assert add_blocked_underwater(1.0, 100.0, 90.0, 0.0) is False   # 关闭


def test_boundary():
    # 恰好 −8bp:不拦(严格小于才拦)
    assert add_blocked_underwater(1.0, 100.0, 99.92, 8.0) is False
    # 刚过线 −8.01bp ⇒ 拦
    assert add_blocked_underwater(1.0, 100.0, 99.9199, 8.0) is True
