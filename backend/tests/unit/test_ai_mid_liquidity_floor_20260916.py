# -*- coding: utf-8 -*-
"""[调研轮8] AI 中线候选**流动性下限**契约测试。

背景：看板 midlong approve 里混着本所几乎无法交易的标的（实测 AMAT/APE liquidity=0.25），
同一批在 VIP跟投路径被「24h成交额 < 试仓下限 $500k」硬拒 ⇒ AI 中线候选永远"选了也下不了单"。
修复：候选层按看板自带 `market_scores.liquidity`（0~1）过滤，默认下限 0.5。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.auto_coin_selector import _midlong_board_approve_candidates  # noqa: E402


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    """最小 db stub：只实现 execute(...).all()。"""

    def __init__(self, rows):
        self._rows = rows
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = str(sql)
        return _Res(self._rows)


ROWS = [
    ("FET", 0.70, 0.7125),
    ("AMAT", 0.65, 0.25),   # 流动性极差 → 应剔除
    ("DOT", 0.60, 0.90),
    ("APE", 0.58, 0.25),    # 同上
    ("NEWX", 0.55, None),   # 无 liquidity 字段 → fail-open 放行
]


def test_liquidity_floor_drops_illiquid():
    out = _midlong_board_approve_candidates(
        _DB(ROWS), fixed=set(), min_conf=0.4, min_liquidity=0.5,
    )
    syms = [s for s, _c in out]
    assert "FET" in syms and "DOT" in syms
    assert "NEWX" in syms, "缺失 liquidity 分应 fail-open 放行（老数据不误杀）"
    assert "AMAT" not in syms and "APE" not in syms, f"低流动性标的未被剔除: {syms}"


def test_liquidity_floor_zero_disables_filter():
    out = _midlong_board_approve_candidates(
        _DB(ROWS), fixed=set(), min_conf=0.4, min_liquidity=0,
    )
    syms = [s for s, _c in out]
    assert "AMAT" in syms and "APE" in syms, "下限=0 必须回滚为不过滤"


def test_fixed_symbols_still_excluded():
    out = _midlong_board_approve_candidates(
        _DB(ROWS), fixed={"FET"}, min_conf=0.4, min_liquidity=0.5,
    )
    assert "FET" not in [s for s, _c in out]


def test_query_reads_liquidity_from_market_scores():
    """实现契约：流动性来自 market_scores->>'liquidity'（不是另发行情请求）。"""
    db = _DB(ROWS)
    _midlong_board_approve_candidates(db, fixed=set(), min_conf=0.4, min_liquidity=0.5)
    assert "market_scores" in db.sql and "liquidity" in db.sql


def test_default_floor_from_settings(monkeypatch):
    from backend.config import settings as S
    monkeypatch.setattr(S, "MIDLONG_AI_MIN_LIQUIDITY", 0.5, raising=False)
    assert hasattr(S, "MIDLONG_AI_MIN_LIQUIDITY")
    out = _midlong_board_approve_candidates(_DB(ROWS), fixed=set(), min_conf=0.4)
    assert "AMAT" not in [s for s, _c in out]
