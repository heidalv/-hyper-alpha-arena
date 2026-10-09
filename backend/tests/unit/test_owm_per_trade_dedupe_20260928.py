# -*- coding: utf-8 -*-
"""[新目标 R4 · 2026-09-28] **逐笔去重**单测：同一 thesis 的每笔平仓都必须各学一次。

## 动机（生产实测，台账 §100.3-7）
thesis `25e9c215` 关联 **18 笔平仓**（09-07→09-28），却只有 1 次 `owm_bump` ⇒ 17/18 笔对学习
不可见。R22 的按 thesis 去重（注释写"防同一笔双触发"）把"同一笔"放大成了"同一 thesis 的所有笔"。
本修复把去重键改为 `(thesis_id, pnl, paper_position_id)`；开关 `MLTO_OWM_PER_TRADE_DEDUPE`
（默认 true；false = 旧行为）。

约束（必须全部锁住）：
  ① 同一笔（同 thesis + 同 pnl）仍只学一次 —— **不引入双写**；
  ② 同一 thesis 的不同笔（不同 pnl）必须各自学习；
  ③ 历史事件没有 pnl/pid ⇒ 按"未记录"处理（这正是行为变更的目的）；
  ④ 回滚开关 = 恢复按 thesis 去重。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.services.mlto import learning_bridge as LB


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MLTO_OWM_PER_TRADE_DEDUPE", raising=False)
    monkeypatch.delenv("MLTO_OWM_SPLIT_DEDUPE", raising=False)
    yield


class _FakeAnalytics:
    """按 payload 匹配的假事件库；`_has_event_for_trade` 走 `.all()`。"""

    def __init__(self, payloads):
        self._events = [SimpleNamespace(payload_json=json.dumps(p)) for p in payloads]

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def first(self):
        return None

    def all(self):
        return self._events


def _outcome(pnl=2.0, thesis="th-1", pid=4831):
    return SimpleNamespace(
        metadata={
            "thesis_id": thesis,
            "session_id": "fa_7e12e7a1b6",
            "timeframe_tier": "mid",
            "close_reason": "sl",
            "paper_position_id": pid,
        },
        pnl=pnl,
        pnl_pct=0.01,
        strategy_id="auto_test",
        symbol="BTC",
        exit_channel="sl",
        tier="mid",
    )


def _install_fake_thesis_store(monkeypatch, seen):
    import sys

    import backend.services.mlto as _pkg

    class _TS:
        @staticmethod
        def append_event(thesis_id, event_type, payload, db=None):
            seen.setdefault("pm_write", 0)
            if event_type == "postmortem":
                seen["pm_write"] += 1
            seen.setdefault("owm_mark", 0)
            if event_type == "owm_bump":
                seen["owm_mark"] += 1
                seen["owm_mark_payload"] = payload

    monkeypatch.setitem(sys.modules, "backend.services.mlto.thesis_store", _TS)
    monkeypatch.setattr(_pkg, "thesis_store", _TS, raising=False)


# ── ① 开关 ──

def test_switch_default_on_and_fail_closed(monkeypatch):
    assert LB._per_trade_dedupe() is True
    monkeypatch.setenv("MLTO_OWM_PER_TRADE_DEDUPE", "false")
    assert LB._per_trade_dedupe() is False
    monkeypatch.setenv("MLTO_OWM_PER_TRADE_DEDUPE", "garbage")
    assert LB._per_trade_dedupe() is False  # 非法值 fail-closed = 退回旧行为


# ── ② 交易身份判据 ──

def test_trade_matches_pnl_semantics():
    assert LB._trade_matches({"pnl": 2.0}, 2.0, None) is True
    assert LB._trade_matches({"pnl": 2.0000004}, 2.0, None) is True   # 1e-6 容差
    assert LB._trade_matches({"pnl": 3.0}, 2.0, None) is False        # 同 thesis 不同笔
    assert LB._trade_matches({"res": "x"}, 2.0, None) is False        # 历史事件无 pnl ⇒ 不匹配
    assert LB._trade_matches({"pnl": 2.0, "paper_position_id": 4831}, 2.0, 4831) is True
    assert LB._trade_matches({"pnl": 2.0, "paper_position_id": 4831}, 2.0, 9999) is False
    # 一方没有 pid ⇒ 不比较 pid（历史兼容）
    assert LB._trade_matches({"pnl": 2.0}, 2.0, 4831) is True


def test_has_event_for_trade_uses_pnl():
    adb = _FakeAnalytics([
        {"pnl": 1.0, "paper_position_id": 1},   # 上一笔
        {"res": "sources=[llm] delta=-0.0050"},  # 旧式 owm_bump（无 pnl）
    ])
    assert LB._has_event_for_trade("th-1", "postmortem", 1.0, 1, adb) is True   # 同一笔
    assert LB._has_event_for_trade("th-1", "postmortem", 2.0, 2, adb) is False  # 不同笔
    assert LB._has_event_for_trade("th-1", "owm_bump", 2.0, 2, adb) is False    # 旧标记不匹配


# ── ③ record_outcome：同一 thesis 的**不同笔**必须各自学习 ──

def test_second_trade_on_same_thesis_learns(monkeypatch):
    seen = {}
    _install_fake_thesis_store(monkeypatch, seen)
    monkeypatch.setattr(LB, "_bump_owm", lambda *a, **k: "sources=[llm] delta=-0.0050")
    # thesis 上已有一笔 pnl=1.0 的 postmortem + owm_bump（模拟 09-24 的那一笔）
    adb = _FakeAnalytics([
        {"pnl": 1.0, "paper_position_id": 4700, "close_reason": "sl"},
        {"res": "x", "pnl": 1.0, "paper_position_id": 4700},
    ])
    LB.record_outcome(None, _outcome(pnl=2.0, pid=4831), analytics_db=adb)
    assert seen.get("pm_write") == 1, "不同笔必须写自己的 postmortem"
    assert seen.get("owm_mark") == 1, "不同笔必须 bump 自己的 OWM"
    pl = seen.get("owm_mark_payload") or {}
    assert pl.get("pnl") == 2.0 and pl.get("paper_position_id") == 4831, "标记必须带交易身份"


def test_same_trade_still_deduped(monkeypatch):
    seen = {}
    _install_fake_thesis_store(monkeypatch, seen)
    monkeypatch.setattr(LB, "_bump_owm", lambda *a, **k: "sources=[llm] delta=-0.0050")
    adb = _FakeAnalytics([
        {"pnl": 2.0, "paper_position_id": 4831, "close_reason": "sl"},   # 同一笔已有 postmortem
        {"res": "x", "pnl": 2.0, "paper_position_id": 4831},            # 同一笔已有 owm 标记
    ])
    LB.record_outcome(None, _outcome(pnl=2.0, pid=4831), analytics_db=adb)
    assert seen.get("pm_write", 0) == 0, "同一笔不得双写 postmortem"
    assert seen.get("owm_mark", 0) == 0, "同一笔不得重复 bump"


# ── ④ 回滚开关 = 按 thesis 去重 ──

def test_rollback_switch_restores_thesis_level(monkeypatch):
    monkeypatch.setenv("MLTO_OWM_PER_TRADE_DEDUPE", "false")
    seen = {}
    _install_fake_thesis_store(monkeypatch, seen)
    monkeypatch.setattr(LB, "_bump_owm", lambda *a, **k: "sources=[llm] delta=-0.0050")
    monkeypatch.setattr(LB, "_has_postmortem", lambda *a, **k: True)
    monkeypatch.setattr(LB, "_has_owm_bump", lambda *a, **k: True)
    LB.record_outcome(None, _outcome(pnl=2.0, pid=4831), None)
    assert seen.get("pm_write", 0) == 0, "回滚后：thesis 已有 postmortem ⇒ 不同笔也被吞（旧行为）"
    assert seen.get("owm_mark", 0) == 0


# ── ⑤ 历史事件无 pnl ⇒ 旧标记不阻挡新笔（行为变更的正面契约） ──

def test_legacy_owm_marker_does_not_block_new_trade(monkeypatch):
    seen = {}
    _install_fake_thesis_store(monkeypatch, seen)
    monkeypatch.setattr(LB, "_bump_owm", lambda *a, **k: "sources=[llm] delta=-0.0050")
    adb = _FakeAnalytics([
        {"res": "sources=[llm] delta=-0.0050"},   # 09-24 的旧标记，无 pnl
    ])
    LB.record_outcome(None, _outcome(pnl=-0.2708, pid=4831), analytics_db=adb)
    assert seen.get("owm_mark") == 1, "旧式无 pnl 的标记不得再吞掉新笔"
