# -*- coding: utf-8 -*-
"""[调研轮10] 中/长线 AI 选币契约测试。

修的两件事：
  1. **候选口径**：实测 72h 内「流动性≥0.35 的非固定币」中 approve=**0**、watch=8
     ⇒ 只认 approve 等于永久空池。改为 approve+watch + 新鲜度窗口 + 流动性下限，
     最终开仓仍由主脑论题与全部入场闸决定。
  2. **长车道 E1 独占**：`TREND_E1_LONG_LANE_EXCLUSIVE=true` 时非 E1 的 long 新开
     只记为提议 ⇒ AI 长线选币永远开不出仓。加 AI 标的窄口径例外。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import trend_e1_engine as e1  # noqa: E402
from backend.services.auto_coin_selector import _midlong_board_approve_candidates  # noqa: E402


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = str(sql)
        self.params = params
        return _Res(self._rows)


# 现场数据（每个 symbol 最新判定）
ROWS = [
    ("DOT", 0.50, "watch", 0.90),
    ("FET", 0.55, "watch", 0.71),
    ("TIA", 0.52, "watch", 0.80),
    ("TON", 0.30, "watch", 0.66),
    ("AAVE", 0.70, "reject", 0.775),
    ("AMAT", 0.65, "approve", 0.25),   # approve 但流动性差 → 仍须剔除
    ("BTC", 1.00, "approve", 1.00),    # 固定币 → 由 fixed 排除
]


def test_watch_admitted_and_illiquid_or_low_conf_dropped(monkeypatch):
    monkeypatch.setenv("MIDLONG_AI_CANDIDATE_VERDICTS", "approve,watch")
    monkeypatch.setenv("MIDLONG_AI_APPROVAL_WINDOW_H", "24")
    out = _midlong_board_approve_candidates(
        _DB(ROWS), fixed={"BTC"}, min_conf=0.40, min_liquidity=0.5,
    )
    syms = [s for s, _c in out]
    assert syms == ["FET", "TIA", "DOT"], syms        # 按 conf 降序
    assert "AAVE" not in syms                          # reject 不入池
    assert "AMAT" not in syms                          # approve 但流动性 0.25 被剔
    assert "TON" not in syms                           # conf 0.30 < 0.40
    assert "BTC" not in syms                           # 固定币排除


def test_approve_only_mode_rollback(monkeypatch):
    """verdict 集合回退为仅 approve 时，watch 一律不入池（只剩 approve 的那条）。"""
    monkeypatch.setenv("MIDLONG_AI_CANDIDATE_VERDICTS", "approve")
    monkeypatch.setenv("MIDLONG_AI_APPROVAL_WINDOW_H", "24")
    rows = [("DOT", 0.50, "watch", 0.90), ("FET", 0.55, "approve", 0.71)]
    out = _midlong_board_approve_candidates(
        _DB(rows), fixed=set(), min_conf=0.40, min_liquidity=0.5,
    )
    assert [s for s, _c in out] == ["FET"], out


def test_window_zero_uses_latest_batch_query(monkeypatch):
    """窗口=0 → 回退旧口径（查询里应是 listed IS TRUE）。"""
    monkeypatch.setenv("MIDLONG_AI_APPROVAL_WINDOW_H", "0")
    db = _DB([("FET", 0.55, 0.71)])
    _midlong_board_approve_candidates(db, fixed=set(), min_conf=0.40, min_liquidity=0.5)
    assert "listed IS TRUE" in db.sql


def test_window_query_is_latest_per_symbol_without_valid_until(monkeypatch):
    monkeypatch.setenv("MIDLONG_AI_APPROVAL_WINDOW_H", "24")
    db = _DB(ROWS)
    _midlong_board_approve_candidates(db, fixed=set(), min_conf=0.40, min_liquidity=0.5)
    assert "DISTINCT ON (upper(symbol))" in db.sql
    assert "valid_until" not in db.sql, "看板 4h 展示 TTL 不应再约束选币视野"
    assert db.params.get("win_h") == 24


# ── 长车道 E1 独占的 AI 例外 ──

def test_long_lane_ai_symbol_allowed(monkeypatch):
    monkeypatch.setattr(e1, "long_lane_exclusive", lambda: True, raising=False)
    monkeypatch.setenv("TREND_E1_LONG_LANE_AI_EXCEPTION", "true")
    monkeypatch.setattr(e1, "is_auto_coin_symbol", lambda *a, **k: True, raising=False)
    import backend.services.auto_coin_selector as acs
    monkeypatch.setattr(acs, "is_auto_coin_symbol", lambda sym, sid=None: sym == "FET", raising=False)
    ok, why = e1.long_lane_open_allowed("long", "trend_follow", None, "open", symbol="FET", session_id="fa_x")
    assert ok and "AI" in why


def test_long_lane_non_ai_still_blocked(monkeypatch):
    monkeypatch.setattr(e1, "long_lane_exclusive", lambda: True, raising=False)
    monkeypatch.setenv("TREND_E1_LONG_LANE_AI_EXCEPTION", "true")
    import backend.services.auto_coin_selector as acs
    monkeypatch.setattr(acs, "is_auto_coin_symbol", lambda sym, sid=None: False, raising=False)
    ok, why = e1.long_lane_open_allowed("long", "trend_follow", None, "open", symbol="DOGE", session_id="fa_x")
    assert not ok and "独占" in why


def test_long_lane_ai_exception_rollback(monkeypatch):
    monkeypatch.setattr(e1, "long_lane_exclusive", lambda: True, raising=False)
    monkeypatch.setenv("TREND_E1_LONG_LANE_AI_EXCEPTION", "false")
    import backend.services.auto_coin_selector as acs
    monkeypatch.setattr(acs, "is_auto_coin_symbol", lambda sym, sid=None: True, raising=False)
    ok, _why = e1.long_lane_open_allowed("long", "trend_follow", None, "open", symbol="FET", session_id="fa_x")
    assert not ok, "关闭例外后必须回到完全独占"


def test_long_lane_signature_accepts_symbol():
    params = inspect.signature(e1.long_lane_open_allowed).parameters
    assert "symbol" in params and "session_id" in params, "调用点需要传 symbol/session 才能判定 AI 来源"
