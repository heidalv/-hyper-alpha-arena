# -*- coding: utf-8 -*-
"""[2026-09-10 §61] 同标的并发开仓「可观测」契约测试。

背景（§61 实证，269 笔已平仓 mid/long）：

| 组 | n | 均值 | 中位 | 胜率 | 合计 |
|---|---|---|---|---|---|
| 同标的并发 | 18 | **-2.39%** | -1.29% | 27.8% | -$36.50 |
| 同标的单笔 | 251 | +3.57% | -0.27% | 35.9% | -$152.55 |

bootstrap 均值差 **-5.96%（95%CI [-10.83%, -1.51%]）**，剔除最差 1/2/3 笔后仍显著（-6.2%~-6.7%）；
但**中位差 CI 跨 0**（-1.02%，[-3.66%, +0.41%]）⇒ 差异在尾部（并发时亏得更深），典型笔无差别。

本轮**不改判据**（每标的并发上限属决策 P13），只把该模式做成**可观测**：收口点闸在
「该标的已有持仓」时打一条 INFO，使该模式在生产日志与后续审计里可被计数。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.services.mlto.midlong_portfolio_risk import choke_point_open_allowed  # noqa: E402


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _DB:
    """按模型类型返回持仓/余额的极简桩。"""

    def __init__(self, positions, equity=4700.0):
        self._pos = positions
        self._eq = equity

    def query(self, model):
        name = getattr(model, "__name__", "")
        if "Balance" in name:
            return _Q([_Row(total_equity=self._eq)])
        return _Q(self._pos)


def _pos(sym, side="long", size=1.0, px=100.0, tier="mid", nature="swing", mark=None):
    return _Row(symbol=sym, side=side, size=size, entry_price=px,
                mark_price=(mark if mark is not None else px), margin=size * px / 3,
                trade_nature=nature, timeframe_tier=tier)


def test_same_symbol_concurrency_is_logged(monkeypatch, caplog):
    from backend.config import settings
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_NET_EXPOSURE_PCT", 5.0, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", 9, raising=False)

    # 用非簇币（BTC/ETH/SOL 之外的）避免相关簇闸先拦掉；同时验证日志仍会打
    db = _DB([_pos("UNI"), _pos("VIRTUAL")])
    with caplog.at_level(logging.INFO):
        ok, why = choke_point_open_allowed(
            db, 14, symbol="UNI", action="buy", tier="mid", trade_nature="swing",
            new_notional=100.0,
        )
    assert ok is True, why
    msgs = [r.getMessage() for r in caplog.records]
    assert any("同标的并发开仓" in m and "已开 1 笔" in m for m in msgs), msgs


def test_no_log_when_symbol_not_held(monkeypatch, caplog):
    from backend.config import settings
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_NET_EXPOSURE_PCT", 5.0, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", 9, raising=False)

    db = _DB([_pos("BTC")])
    with caplog.at_level(logging.INFO):
        ok, _ = choke_point_open_allowed(
            db, 14, symbol="ETH", action="buy", tier="mid", trade_nature="swing",
            new_notional=100.0,
        )
    assert ok is True
    assert not [m for m in (r.getMessage() for r in caplog.records) if "同标的并发开仓" in m]


def test_behavior_unchanged_non_midlong(monkeypatch):
    """seal：非 mid/long 仍直接放行（scalp 不受该闸影响）。"""
    ok, why = choke_point_open_allowed(
        _DB([]), 14, symbol="ETH", action="buy", tier="short", trade_nature="scalp",
        new_notional=100.0,
    )
    assert ok is True and why == "not_midlong"
