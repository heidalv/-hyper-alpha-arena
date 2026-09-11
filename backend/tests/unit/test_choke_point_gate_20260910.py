# -*- coding: utf-8 -*-
"""[2026-09-10 第 12 轮] 组合闸**收口点**契约测试（§48.5 落地）。

先写测试、再改码（本轮按此顺序执行）。锁三件事：
  1. **只对 mid/long 生效**：scalp/short/日内一律 `not_midlong` 直接放行（不影响其它车道）；
  2. **真的会拦**：mid/long 达上限时返回 False 且理由可辨识；
  3. **幂等 / 只读**：连续调用两次结果一致，且不因检查而改变任何状态（无写操作）。
额外：`place_order` 必须真的调用该收口闸（源码护栏）。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


class _Q:
    """极简 query 桩：filter/first/all。"""

    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _DB:
    """按模型类型返回不同桩数据。"""

    def __init__(self, positions, equity=4710.0):
        self._positions = positions
        self._equity = equity
        self.writes = 0

    def query(self, model, *a, **k):
        name = getattr(model, "__name__", "")
        if name == "PaperPosition":
            return _Q(self._positions)
        if name == "PaperBalance":
            return _Q([_Row(total_equity=self._equity)])
        return _Q([])

    def add(self, *a, **k):
        self.writes += 1

    def commit(self):
        self.writes += 1


def _pos(sym, tier="long", nature="position", side="long", size=1.0, px=100.0, margin=30.0):
    return _Row(symbol=sym, side=side, size=size, entry_price=px, mark_price=px,
                margin=margin, trade_nature=nature, timeframe_tier=tier)


def _settings(monkeypatch, cap=4):
    from backend.config import settings
    monkeypatch.setattr(settings, "MIDLONG_PORTFOLIO_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", cap, raising=False)
    monkeypatch.setattr(settings, "MIDLONG_CORR_CLUSTER_SYMBOLS", "", raising=False)
    return settings


def test_non_midlong_is_untouched(monkeypatch):
    """scalp / short / 日内不得被组合闸影响。"""
    _settings(monkeypatch, cap=1)
    from backend.services.mlto.midlong_portfolio_risk import choke_point_open_allowed

    db = _DB([_pos("AAA", tier="long", nature="position")])  # 已满 1 笔
    for tier, nature in (("short", "scalp"), ("short", "intraday"), ("", "scalp"), ("short", "")):
        ok, why = choke_point_open_allowed(
            db, 14, symbol="DOGE", action="buy", tier=tier, trade_nature=nature,
            new_notional=500.0,
        )
        assert ok and why == "not_midlong", f"tier={tier} nature={nature} 应完全放行，得到 {ok}/{why}"


def test_midlong_blocks_at_cap(monkeypatch):
    _settings(monkeypatch, cap=3)
    from backend.services.mlto.midlong_portfolio_risk import choke_point_open_allowed

    db = _DB([_pos("SOL"), _pos("ETH"), _pos("XRP", tier="mid", nature="swing")])
    ok, why = choke_point_open_allowed(
        db, 14, symbol="VIRTUAL", action="buy", tier="long", trade_nature="trend_follow",
        new_notional=900.0,
    )
    assert not ok, why
    assert "midlong_open_positions" in why


def test_midlong_allows_below_cap(monkeypatch):
    _settings(monkeypatch, cap=4)
    from backend.services.mlto.midlong_portfolio_risk import choke_point_open_allowed

    db = _DB([_pos("SOL"), _pos("ETH")])
    ok, why = choke_point_open_allowed(
        db, 14, symbol="VIRTUAL", action="buy", tier="long", trade_nature="trend_follow",
        new_notional=900.0,
    )
    assert ok, why


def test_idempotent_and_read_only(monkeypatch):
    """连续两次结果一致，且检查过程不产生任何写操作。"""
    _settings(monkeypatch, cap=3)
    from backend.services.mlto.midlong_portfolio_risk import choke_point_open_allowed

    db = _DB([_pos("SOL"), _pos("ETH"), _pos("XRP")])
    a1 = choke_point_open_allowed(db, 14, symbol="VIRTUAL", action="buy",
                                 tier="long", trade_nature="trend_follow", new_notional=900.0)
    a2 = choke_point_open_allowed(db, 14, symbol="VIRTUAL", action="buy",
                                 tier="long", trade_nature="trend_follow", new_notional=900.0)
    assert a1 == a2, "两次检查结果必须一致（幂等）"
    assert db.writes == 0, "组合闸检查必须是只读的（不得产生写操作）"


def test_place_order_calls_choke_gate():
    """源码护栏：收口点必须真的调用组合闸（否则覆盖率修复等于没做）。"""
    src = (ROOT / "backend" / "services" / "paper_trading_engine.py").read_text(encoding="utf-8")
    m = re.search(r"def place_order\(", src)
    assert m
    seg = src[m.end(): m.end() + 6000]
    assert "choke_point_open_allowed" in seg, "place_order 未接入组合闸收口点（§48.5）"
    assert "_ck_ok" in seg and "return None" in seg, "接入后必须能拒单"
    # 失败必须可见
    idx = seg.find("MidLongChokeGate] 检查异常")
    assert idx > 0 and "warning" in seg[max(0, idx - 200): idx + 200]
