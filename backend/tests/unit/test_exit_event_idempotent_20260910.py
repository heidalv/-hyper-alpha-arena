# -*- coding: utf-8 -*-
"""[2026-09-10 第二十八轮] 平仓事件幂等契约测试（§38.5）。

实测 #4638（VIRTUAL mid，9/9 23:21）在**一次**平仓里被两条并发通道各写一次
`final_trade_outcome`（sl −40.548 → thesis_invalidation −41.885），hard_line 再补登一次
`hard_line_close` → 3 个事件；仓位终值只有一份（reduce_count=0、总 USD 只算一次），
但落库 reason 不是首个触发通道。事件层加幂等后应只剩一条 `final_trade_outcome`。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


class _FakeQuery:
    def __init__(self, first_val):
        self._v = first_val

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._v


class _FakeDB:
    """只实现 _record_exit_event 用到的 query/add/rollback。"""

    def __init__(self, existing_final=None):
        self.existing = existing_final
        self.added = []

    def query(self, *a, **k):
        return _FakeQuery(self.existing)

    def add(self, obj):
        self.added.append(obj)

    def rollback(self):
        pass


class _Pos:
    id = 4638
    account_id = 14
    strategy_id = "tpl_mid_range_x"
    symbol = "VIRTUAL"
    side = "long"
    trade_nature = "swing"
    peak_unrealized_pnl = 5.0
    unrealized_pnl = -41.88
    peak_pnl_pct = 0.0071
    trough_pnl_pct = -0.0458
    trough_unrealized_pnl = -37.59


def _engine():
    from backend.services.paper_trading_engine import paper_engine
    return paper_engine


def test_final_trade_outcome_is_idempotent():
    """已存在 final_trade_outcome 时不得再写第二条。"""
    db = _FakeDB(existing_final=1)          # 模拟首条已落库
    _engine()._record_exit_event(
        db, _Pos(), event_type="final_trade_outcome",
        price=0.6868, quantity=1134.39, pnl=-41.88, fee=0.312, close_ratio=1.0,
        exit_channel="thesis_invalidation", metadata={"reason": "thesis_invalidation"},
    )
    assert db.added == [], "重复的 final_trade_outcome 应被幂等跳过"


def test_first_final_trade_outcome_is_written():
    """没有历史终局事件时必须正常写入。"""
    db = _FakeDB(existing_final=None)
    _engine()._record_exit_event(
        db, _Pos(), event_type="final_trade_outcome",
        price=0.6880, quantity=1134.39, pnl=-40.55, fee=0.312, close_ratio=1.0,
        exit_channel="sl", metadata={"reason": "sl"},
    )
    assert len(db.added) == 1
    ev = db.added[0]
    assert str(ev.event_type) == "final_trade_outcome"
    assert str(ev.exit_channel) == "sl"


def test_partial_exit_event_not_blocked_by_final():
    """幂等只作用于 final_trade_outcome：部分平仓事件不受影响。"""
    db = _FakeDB(existing_final=1)
    _engine()._record_exit_event(
        db, _Pos(), event_type="partial_exit_event",
        price=0.6749, quantity=666.70, pnl=-14.0, fee=0.1, close_ratio=0.5,
        exit_channel="trend_weaken", metadata={"reason": "trend_weaken"},
    )
    assert len(db.added) == 1, "partial_exit_event 不应被 final 的幂等逻辑拦住"


def test_hard_line_close_not_blocked_by_final():
    """hard_line_close 属补充通道事件，保持可写（真正的幂等只针对终局腿）。"""
    db = _FakeDB(existing_final=1)
    _engine()._record_exit_event(
        db, _Pos(), event_type="hard_line_close",
        exit_channel="sl", metadata={"exit_source": "stop_loss", "channel": "hard_line_direct"},
    )
    assert len(db.added) == 1
