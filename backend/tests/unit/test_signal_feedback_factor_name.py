# -*- coding: utf-8 -*-
"""[fix 2026-08-29] 因子快照名不再被 28 字符截断（迁移 0008 已扩列到 100）。

背景：52/92 个 AI 生成因子的 `factor:<name>` 超过 28 字符，被截断后
factor_ic_evaluator 按截断名写运行时权重，pipeline 按全名查表 miss，
学习权重静默失效。契约：≤100 字符的因子名必须原样落库。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.signal_feedback_tracker import SignalFeedbackTracker


class _FakeDB:
    """最小 db 桩：捕获 bulk_save_objects 的对象并立刻可见。"""

    def __init__(self):
        self.saved = []

    def bulk_save_objects(self, objs):
        self.saved.extend(objs)

    def commit(self):
        pass

    def rollback(self):
        pass


def _record(factor_values):
    db = _FakeDB()
    SignalFeedbackTracker().record_entry_signals(
        db, account_id=1, trade_id=1, symbol="BTC", side="long",
        active_signals={}, factor_values=factor_values,
    )
    return [o.signal_type for o in db.saved]


def test_long_factor_name_not_truncated():
    long_name = "ai_gen_liquidity_squeeze"  # factor: 前缀后 31 字符，旧截断会截成 28
    types = _record({long_name: 0.5})
    assert f"factor:{long_name}" in types


def test_name_roundtrip_matches_pipeline_lookup_key():
    # 权重闭环：落库名（去掉 factor: 前缀）必须等于因子引擎里的全名
    name = "ai_gen_regime_uncertainty"
    types = _record({name: -0.3})
    stored = [t[len("factor:"):] for t in types if t.startswith("factor:")]
    assert stored == [name]


def test_over_100_chars_still_defensively_truncated():
    name = "x" * 150
    types = _record({name: 0.1})
    assert all(len(t) <= 100 for t in types)
    assert f"factor:{name}"[:100] in types
