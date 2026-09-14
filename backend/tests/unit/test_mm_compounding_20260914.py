# -*- coding: utf-8 -*-
"""[2026-09-14 F84/F85] 复利契约测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import QuoteParams  # noqa: E402


def test_quote_params_has_compound_ratio():
    """F85：compound_ratio 必须在 QuoteParams 字段（注册表映射依赖）。"""
    p = QuoteParams(compound_ratio=1.0)
    assert p.compound_ratio == 1.0
    assert "compound_ratio" in QuoteParams.__dataclass_fields__


def test_runner_compound_defaults_off():
    """默认 0 = 固定腿量（旧行为不变）。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    assert r.compound_ratio == 0.0


def test_runner_compound_reads_params():
    """compound_ratio 从 QuoteParams 传入。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"],
                              params=QuoteParams(compound_ratio=0.5))
    assert r.compound_ratio == 0.5


def test_tick_applies_compounding():
    """tick() 必须含复利权益读取分支（源契约）。"""
    import inspect
    src = inspect.getsource(mmrunner.ShadowRunner.tick)
    assert "_read_account_equity" in src
    assert "compound_ratio" in src
    assert "self.fill_notional = max(10.0" in src


def test_read_account_equity_guarded():
    """无 account_id 时返回 0，不抛异常。"""
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    assert r._read_account_equity() == 0.0
